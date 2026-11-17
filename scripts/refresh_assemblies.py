#!/usr/bin/env python3
"""Refresh AssemblyGrasp on TheBelly: download, rebuild, upload, optionally activate.

Python 3.8+ standard library and the three sibling preprocessing scripts.
AWS CLI is needed for upload/activation. No genome sequences are downloaded.
Default: current GenBank + RefSeq summaries and fresh BioProject XML.
Each run gets a new local directory and immutable S3 prefix. No old data is
deleted. --activate explicitly opts into updating the existing Lambda config.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET

ASSEMBLY_ROOT = "https://ftp.ncbi.nlm.nih.gov/genomes/ASSEMBLY_REPORTS/"
ASSEMBLY_URLS = {name: ASSEMBLY_ROOT + "assembly_summary_" + name + ".txt" for name in ("genbank", "refseq")}
BIOPROJECT_URL = "https://ftp.ncbi.nlm.nih.gov/bioproject/bioproject.xml"
DATASET_VARIABLES = ("ASSEMBLY_BUCKET", "ASSEMBLY_MANIFEST_KEY", "ASSEMBLY_KEYWORD_INDEX_KEY")


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
    os.replace(str(temporary), str(path))


def file_info(path, root):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": Path(path).relative_to(root).as_posix(),
            "bytes": Path(path).stat().st_size, "sha256": digest.hexdigest()}


def download(url, destination, attempts=3):
    destination = Path(destination)
    partial = destination.with_suffix(destination.suffix + ".partial")
    if destination.exists():
        raise ValueError("Refusing to replace a completed download: %s" % destination)
    for attempt in range(attempts):
        size, digest = 0, hashlib.sha256()
        try:
            request = urllib.request.Request(url, headers={
                "User-Agent": "AssemblyGrasp-refresh/1.0", "Accept-Encoding": "identity"})
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
                if response.status != 200:
                    raise ValueError("Expected a complete HTTP 200 download")
                expected = response.headers.get("Content-Length")
                metadata = {"url": url, "etag": response.headers.get("ETag"),
                            "last_modified": response.headers.get("Last-Modified")}
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    out.write(block)
                    digest.update(block)
                    size += len(block)
                    if size % (256 * 1024 * 1024) == 0:
                        print("Downloaded {:.0f} MiB: {}".format(size / 1048576, destination.name), flush=True)
                if not size or (expected is not None and size != int(expected)):
                    raise OSError("Incomplete download: %s" % url)
            os.replace(str(partial), str(destination))
            return {**metadata, "bytes": size, "sha256": digest.hexdigest(), "file": destination.name}
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if partial.exists():
                partial.unlink()
            if attempt + 1 == attempts:
                raise RuntimeError("Download failed after retries: %s" % url) from exc
            print("Retrying download: %s" % destination.name, file=sys.stderr, flush=True)
            time.sleep(min(2 ** attempt, 8))


def helper(name):
    path = Path(__file__).resolve().with_name(name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def refresh_lock(directory):
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".refresh.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another refresh is running in %s" % directory) from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class AWS:
    def __init__(self, region, profile=None):
        if not shutil.which("aws"):
            raise RuntimeError("AWS CLI is not installed or not on PATH")
        self.command = ["aws", "--region", region]
        if profile:
            self.command += ["--profile", profile]

    def run(self, arguments, document=None):
        filename = None
        try:
            command = self.command + list(arguments)
            if document is not None:
                # Environment variables can contain secrets. Keep them out of
                # command arguments/logs and remove the mode-0600 input file.
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as stream:
                    filename = stream.name
                    json.dump(document, stream)
                command += ["--cli-input-json", "file://" + filename]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, env={**os.environ, "AWS_PAGER": ""})
            if result.returncode:
                # Do not echo full AWS responses or existing Lambda variables.
                raise RuntimeError("AWS %s failed (exit %d). Check AWS credentials/permissions and retry." % (
                    " ".join(arguments[:2]), result.returncode))
            return result.stdout
        finally:
            if filename:
                os.unlink(filename)


def valid_target(bucket, prefix):
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Expected an S3 bucket name")
    if not prefix or prefix.startswith("/") or ".." in PurePosixPath(prefix).parts:
        raise ValueError("Expected a nonempty relative S3 prefix without '..'")


def prepare_run(workdir, bucket, prefix, source="both", bioproject_xml=None, skip_bioprojects=False):
    valid_target(bucket, prefix)
    tag = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run = Path(workdir).expanduser().resolve() / tag
    run.mkdir(parents=True, exist_ok=False)
    sources = run / "sources"
    sources.mkdir()
    report = {"schema_version": 1, "status": "preparing", "run_id": tag,
              "bucket": bucket, "prefix": prefix.strip("/") + "/" + tag,
              "assembly_source": source, "sources": []}
    write_json(run / "refresh.json", report)
    print("Refresh directory: %s" % run, flush=True)
    try:
        names = ["genbank", "refseq"] if source == "both" else [source]
        for name in names:
            path = sources / (name + ".tsv")
            report["sources"].append(download(ASSEMBLY_URLS[name], path))
        combined = sources / "ncbi_assembly_info.tsv"
        with combined.open("wb") as out:
            for name in names:
                with (sources / (name + ".tsv")).open("rb") as inp:
                    shutil.copyfileobj(inp, out, 1024 * 1024)
                out.write(b"\n")
        print("Preparing assembly chunks", flush=True)
        raw = run / ("dataset" if skip_bioprojects else "raw-chunks")
        manifest = helper("prepare_assembly_chunks").prepare(str(combined), raw)
        if not manifest["total_rows"]:
            raise ValueError("No assembly rows found; refusing to publish an empty dataset")
        if not skip_bioprojects:
            if bioproject_xml:
                xml = Path(bioproject_xml).expanduser().resolve()
                # Explicitly record when the owner reuses a local XML snapshot.
                report["sources"].append({**file_info(xml, xml.parent), "reused_local_file": str(xml)})
            else:
                xml = sources / "bioproject.xml"
                report["sources"].append(download(BIOPROJECT_URL, xml))
            print("Adding BioProject metadata", flush=True)
            manifest = helper("enrich_bioprojects").enrich(raw, xml, run / "dataset", run / "bioproject-cache.sqlite")
        print("Building organism/BioProject search catalog", flush=True)
        catalog = helper("build_keyword_index").build(run / "dataset", run / "keyword-v1")
        report.update({"status": "prepared", "dataset_id": manifest["dataset_id"],
                       "rows": manifest["total_rows"], "keyword_index_bytes": catalog["index_bytes"],
                       "manifest_key": report["prefix"] + "/manifest.json",
                       "keyword_key": report["prefix"] + "/keyword-v1/organism_names.sqlite"})
        report["files"] = [file_info(path, run) for folder in ("dataset", "keyword-v1")
                           for path in sorted((run / folder).rglob("*")) if path.is_file()]
        write_json(run / "refresh.json", report)
        return run, report
    except Exception:
        report["status"] = "preparation_failed"
        write_json(run / "refresh.json", report)
        raise


def validate_prepared(run, report):
    valid_target(report["bucket"], report["prefix"])
    if report.get("status") not in ("prepared", "uploaded", "activation_requested", "active") or not report.get("files"):
        raise ValueError("This refresh did not finish preparation; start a new refresh")
    for expected in report["files"]:
        relative = PurePosixPath(expected["path"])
        path = (run / str(relative)).resolve()
        if relative.is_absolute() or ".." in relative.parts or run not in path.parents:
            raise ValueError("Invalid prepared-file path")
        if file_info(path, run) != expected:
            raise ValueError("Prepared file changed or is incomplete: %s" % relative)
    manifest = json.loads((run / "dataset/manifest.json").read_text(encoding="utf-8"))
    catalog = json.loads((run / "keyword-v1/keyword_index.json").read_text(encoding="utf-8"))
    if manifest["dataset_id"] != report["dataset_id"] or catalog["dataset_id"] != report["dataset_id"] or manifest["total_rows"] != catalog["rows"]:
        raise ValueError("Manifest/catalog dataset or row counts do not match")
    if report["manifest_key"] != report["prefix"] + "/manifest.json" or report["keyword_key"] != report["prefix"] + "/keyword-v1/organism_names.sqlite":
        raise ValueError("Prepared S3 keys do not match the versioned prefix")


def publish(run, report, aws):
    validate_prepared(run, report)
    if report["status"] != "prepared":
        print("This refresh was already uploaded; its immutable objects are retained", flush=True)
        return
    target = "s3://" + report["bucket"] + "/" + report["prefix"] + "/"
    print("Uploading new dataset: %s" % target, flush=True)
    aws.run(["s3", "sync", str(run / "dataset") + "/", target,
             "--exclude", "manifest.json", "--only-show-errors"])
    aws.run(["s3", "sync", str(run / "keyword-v1") + "/", target + "keyword-v1/", "--only-show-errors"])
    # The manifest is the publication boundary. No activation before this succeeds.
    aws.run(["s3", "cp", str(run / "dataset/manifest.json"), target + "manifest.json", "--only-show-errors"])
    report["status"] = "uploaded"
    write_json(run / "refresh.json", report)


def activate(run, report, aws, function_name):
    if report["status"] not in ("uploaded", "activation_requested", "active"):
        raise ValueError("Upload must finish before activation")
    if report.get("function_name") and report["function_name"] != function_name:
        raise ValueError("This refresh's activation record belongs to another Lambda function")
    config = json.loads(aws.run(["lambda", "get-function-configuration", "--function-name", function_name, "--output", "json"]))
    if config.get("LastUpdateStatus") == "InProgress" or config.get("Environment", {}).get("Error"):
        raise RuntimeError("Lambda configuration is updating or its environment could not be read; retry later")
    required_mb = math.ceil(report["keyword_index_bytes"] / 1048576) + 256
    if config.get("EphemeralStorage", {}).get("Size", 512) < required_mb:
        raise RuntimeError("Increase Lambda ephemeral storage to at least %d MB before activation" % required_mb)
    variables = dict(config.get("Environment", {}).get("Variables", {}))
    target = dict(zip(DATASET_VARIABLES, (report["bucket"], report["manifest_key"], report["keyword_key"])))
    if all(variables.get(key) == value for key, value in target.items()):
        report["status"] = "active"
        write_json(run / "refresh.json", report)
        print("Lambda already points to this dataset", flush=True)
        return
    if "RevisionId" not in config:
        raise RuntimeError("Lambda configuration has no revision ID")
    rollback_path = run / "previous-dataset.json"
    if not rollback_path.exists():
        write_json(rollback_path, {"function_name": function_name, "variables": {key: variables.get(key) for key in DATASET_VARIABLES}})
    variables.update(target)
    report.update({"status": "activation_requested", "function_name": function_name})
    write_json(run / "refresh.json", report)
    aws.run(["lambda", "update-function-configuration"], document={
        "FunctionName": function_name, "RevisionId": config["RevisionId"],
        "Environment": {"Variables": variables}})
    aws.run(["lambda", "wait", "function-updated", "--function-name", function_name])
    updated = json.loads(aws.run(["lambda", "get-function-configuration", "--function-name", function_name, "--output", "json"]))
    if any(updated.get("Environment", {}).get("Variables", {}).get(key) != value for key, value in target.items()):
        raise RuntimeError("Lambda settings changed during activation; check the current configuration")
    report["status"] = "active"
    write_json(run / "refresh.json", report)
    print("Activated dataset %s (%s assemblies)" % (report["dataset_id"], format(report["rows"], ",")), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", default="~/databases/assembly-grasp-refreshes")
    parser.add_argument("--bucket", default="assembly-grasp-bucket")
    parser.add_argument("--prefix", default="assembly-index")
    parser.add_argument("--region", default="us-east-2")
    parser.add_argument("--profile", help="Optional AWS CLI profile")
    parser.add_argument("--assembly-source", choices=("both", "genbank", "refseq"), default="both")
    parser.add_argument("--bioproject-xml", help="Reuse a local XML snapshot instead of downloading a fresh one")
    parser.add_argument("--skip-bioprojects", action="store_true", help="Explicitly omit project enrichment/search from the new dataset")
    parser.add_argument("--prepare-only", action="store_true", help="Build locally without uploading or activating")
    parser.add_argument("--publish", metavar="RUN_DIRECTORY", help="Upload/retry an already prepared refresh without rebuilding")
    parser.add_argument("--function-name", help="Existing Lambda function name (not its URL)")
    parser.add_argument("--activate", action="store_true", help="After upload, switch both index keys on the existing Lambda")
    args = parser.parse_args()
    if args.activate and (not args.function_name or args.prepare_only):
        parser.error("--activate requires --function-name and cannot be used with --prepare-only")
    if args.publish and args.prepare_only:
        parser.error("--publish cannot be used with --prepare-only")
    try:
        aws = None if args.prepare_only else AWS(args.region, args.profile)
        root = Path(args.publish).expanduser().resolve().parent if args.publish else Path(args.workdir).expanduser().resolve()
        with refresh_lock(root):
            if args.publish:
                run = Path(args.publish).expanduser().resolve()
                report = json.loads((run / "refresh.json").read_text(encoding="utf-8"))
                aws = AWS(report.get("region", args.region), args.profile)
            else:
                run, report = prepare_run(root, args.bucket, args.prefix.rstrip("/"), args.assembly_source,
                                          args.bioproject_xml, args.skip_bioprojects)
                report["region"] = args.region
                write_json(run / "refresh.json", report)
            if not args.prepare_only:
                publish(run, report, aws)
                if args.activate:
                    activate(run, report, aws, args.function_name)
            print("Refresh report: %s" % (run / "refresh.json"))
            print("ASSEMBLY_MANIFEST_KEY=" + report["manifest_key"])
            print("ASSEMBLY_KEYWORD_INDEX_KEY=" + report["keyword_key"])
            if report["status"] != "active":
                print("Not activated. Use --publish RUN_DIRECTORY --activate --function-name NAME when ready.")
    except (OSError, ValueError, RuntimeError, KeyError, sqlite3.Error, ET.ParseError) as exc:
        parser.exit(1, "ERROR: %s\nPrepared/downloaded files are retained; see the refresh report before retrying.\n" % exc)


if __name__ == "__main__":
    main()
