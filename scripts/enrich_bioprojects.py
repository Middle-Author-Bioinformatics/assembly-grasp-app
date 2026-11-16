#!/usr/bin/env python3
"""Add BioProject titles/descriptions to a NEW copy of prepared assembly chunks.

Python 3.8+, standard library only. Input: NCBI's bulk bioproject.xml (or .gz).
Original chunks/XML are never modified. A reusable SQLite cache is built beside
the XML, unless --cache-db is specified. Upload the output to a NEW S3 prefix.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
import gzip
from html.parser import HTMLParser
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET

ADDED_COLUMNS = ["bioproject_title", "bioproject_description"]
ACCESSION = re.compile(r"PRJ(?:NA|EB|DB)\d+", re.I)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"p", "br", "div", "li"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li"}:
            self.parts.append(" ")

    def handle_data(self, data):
        self.parts.append(data)


def clean_text(value):
    # Older NCBI records contain HTML encoded inside XML text. Keep plain text
    # and collapse tabs/newlines so each assembly remains exactly one TSV row.
    parser = PlainText()
    parser.feed(value or "")
    parser.close()
    return " ".join("".join(parser.parts).split())


def project_records(source):
    opener = gzip.open if str(source).lower().endswith(".gz") else open
    with opener(source, "rb") as stream:
        stack = []
        for event, node in ET.iterparse(stream, events=("start", "end")):
            if event == "start":
                node.tag = node.tag.rsplit("}", 1)[-1]
                stack.append(node)
                continue
            if node.tag == "Project" and node.find("ProjectID") is not None:
                description = node.find("ProjectDescr")
                if description is not None:
                    title_node = description.find("Title")
                    desc_node = description.find("Description")
                    title = clean_text("".join(title_node.itertext()) if title_node is not None else "")
                    text = clean_text("".join(desc_node.itertext()) if desc_node is not None else "")
                    # Include replaced/secondary accessions only when explicitly
                    # listed in this record; never inherit umbrella project text.
                    for identifier in node.find("ProjectID"):
                        accession = identifier.get("accession", "").upper()
                        if ACCESSION.fullmatch(accession):
                            yield accession, title, text
                node.clear()
                if len(stack) > 1:
                    stack[-2].remove(node)
            elif node.tag == "Package":
                node.clear()
                if len(stack) > 1:
                    stack[-2].remove(node)
            stack.pop()


def project_cache(xml_path, cache_path):
    source = Path(xml_path).expanduser().resolve()
    cache = Path(cache_path).expanduser().resolve()
    if source == cache:
        raise ValueError("The cache must not replace the BioProject XML")
    stat = source.stat()
    signature = json.dumps([str(source), stat.st_size, stat.st_mtime_ns])
    if cache.exists():
        with closing(sqlite3.connect("file:" + cache.as_posix() + "?mode=ro", uri=True)) as conn:
            metadata = dict(conn.execute("SELECT key, value FROM metadata"))
            if metadata.get("source_signature") == signature and metadata.get("cache_version") == "1":
                print("Reusing BioProject cache: %s" % cache, file=sys.stderr)
                return cache
        raise ValueError("Cache belongs to another XML snapshot; choose a new --cache-db path")
    cache.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".bioproject-cache-", dir=str(cache.parent))
    os.close(fd)
    partial = Path(name)
    try:
        with closing(sqlite3.connect(str(partial))) as conn:
            conn.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE projects (accession TEXT PRIMARY KEY, title TEXT NOT NULL,
                    description TEXT NOT NULL) WITHOUT ROWID;
            """)
            batch, total = [], 0
            for record in project_records(source):
                batch.append(record)
                total += 1
                if len(batch) == 5000:
                    conn.executemany("INSERT OR REPLACE INTO projects VALUES (?, ?, ?)", batch)
                    batch.clear()
                if total % 100000 == 0:
                    print("Read {:,} BioProject accessions".format(total), file=sys.stderr, flush=True)
            if batch:
                conn.executemany("INSERT OR REPLACE INTO projects VALUES (?, ?, ?)", batch)
            if not total:
                raise ValueError("No BioProject records found in XML")
            conn.executemany("INSERT INTO metadata VALUES (?, ?)", [
                ("cache_version", "1"), ("source_signature", signature),
            ])
            conn.commit()
        os.replace(str(partial), str(cache))
        print("Cached {:,} BioProject accessions".format(total), file=sys.stderr)
        return cache
    finally:
        if partial.exists():
            partial.unlink()


def enrich(chunks_dir, bioproject_xml, outdir, cache_db=None):
    root = Path(chunks_dir).expanduser().resolve()
    source_manifest = root / "manifest.json"
    manifest = json.loads(source_manifest.read_text(encoding="utf-8"))
    columns = manifest["columns"]
    if manifest.get("schema_version") != 1 or "bioproject" not in columns or "organism_name" not in columns:
        raise ValueError("Expected a prepared manifest with bioproject and organism_name columns")
    if any(c in columns for c in ADDED_COLUMNS):
        raise ValueError("Input is already enriched; start with the original assembly chunks")
    destination = Path(outdir).expanduser().resolve()
    if destination.exists():
        raise ValueError("Output already exists; choose a new directory")
    if root in destination.parents:
        raise ValueError("Output must be outside the input chunk directory")
    cache = project_cache(bioproject_xml, cache_db or str(bioproject_xml) + ".assemblygrasp.sqlite")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".assembly-bioprojects-", dir=str(destination.parent)))
    new_columns = columns + ADDED_COLUMNS
    bp_idx = columns.index("bioproject")
    total = with_accession = matched = with_text = 0
    missing = set()
    try:
        with closing(sqlite3.connect("file:" + cache.as_posix() + "?mode=ro", uri=True)) as conn:
            @lru_cache(maxsize=32768)
            def lookup(accession):
                return conn.execute("SELECT title, description FROM projects WHERE accession=?", (accession,)).fetchone()

            for group in manifest["groups"].values():
                for chunk in group["chunks"]:
                    key = chunk["key"]
                    if PurePosixPath(key).is_absolute() or ".." in PurePosixPath(key).parts:
                        raise ValueError("Invalid chunk path")
                    path = (root / key).resolve()
                    if root not in path.parents:
                        raise ValueError("Chunk path escapes input directory")
                    output = staging / key
                    output.parent.mkdir(parents=True, exist_ok=True)
                    count = 0
                    with path.open(encoding="utf-8-sig") as inp, output.open("w", encoding="utf-8", newline="") as out:
                        out.write("\n".join(manifest.get("comments", []) + ["#" + "\t".join(new_columns)]) + "\n")
                        for line in inp:
                            line = line.rstrip("\r\n")
                            if not line or line.startswith("#"):
                                continue
                            cells = line.split("\t")
                            if len(cells) != len(columns):
                                raise ValueError("Malformed assembly row in %s" % key)
                            accessions = list(dict.fromkeys(ACCESSION.findall(cells[bp_idx].upper())))
                            titles, descriptions = [], []
                            found = False
                            for accession in accessions:
                                data = lookup(accession)
                                if data is None:
                                    missing.add(accession)
                                    continue
                                found = True
                                title, text = data
                                label = accession + ": " if len(accessions) > 1 else ""
                                if title:
                                    titles.append(label + title)
                                if text:
                                    descriptions.append(label + text)
                            title, text = " | ".join(titles), " | ".join(descriptions)
                            out.write("\t".join(cells + [title, text]) + "\n")
                            total += 1
                            count += 1
                            with_accession += bool(accessions)
                            matched += found
                            with_text += bool(title or text)
                            if total % 100000 == 0:
                                print("Enriched {:,} assembly rows".format(total), file=sys.stderr, flush=True)
                    if count != chunk["rows"]:
                        raise ValueError("Row count differs from manifest for %s" % key)
                    chunk["bytes"] = output.stat().st_size
            lookup.cache_clear()
        if total != manifest["total_rows"]:
            raise ValueError("Total row count differs from manifest")
        manifest["parent_dataset_id"] = manifest["dataset_id"]
        manifest["dataset_id"] = str(uuid.uuid4())
        manifest["columns"] = new_columns
        manifest["created_at"] = datetime.now(timezone.utc).isoformat()
        report = {"source_file": Path(bioproject_xml).name, "prepared_at": manifest["created_at"],
                  "assembly_rows": total, "rows_with_bioproject": with_accession,
                  "rows_with_matched_project": matched, "rows_with_project_text": with_text,
                  "missing_project_count": len(missing)}
        manifest["bioproject_enrichment"] = report
        (staging / "bioproject_enrichment.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        (staging / "missing_bioprojects.txt").write_text("\n".join(sorted(missing)) + ("\n" if missing else ""), encoding="utf-8")
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(staging), str(destination))
        print("Prepared {:,} assemblies; {:,} with BioProject text; {:,} unmatched project accessions: {}".format(
            total, with_text, len(missing), destination), file=sys.stderr)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks-dir", required=True)
    parser.add_argument("--bioproject-xml", required=True, help="NCBI bioproject.xml or bioproject.xml.gz")
    parser.add_argument("--outdir", required=True, help="NEW directory for enriched chunks")
    parser.add_argument("--cache-db", help="Reusable SQLite cache; defaults to <XML path>.assemblygrasp.sqlite")
    args = parser.parse_args()
    try:
        enrich(args.chunks_dir, args.bioproject_xml, args.outdir, args.cache_db)
    except (OSError, ValueError, KeyError, sqlite3.Error, ET.ParseError) as exc:
        parser.exit(1, "ERROR: %s\n" % exc)


if __name__ == "__main__":
    main()
