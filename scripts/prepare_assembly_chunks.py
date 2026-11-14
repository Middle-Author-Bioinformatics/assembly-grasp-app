#!/usr/bin/env python3
"""Split an NCBI assembly TSV into indexed organism-prefix chunks.

Python >=3.8, standard library only; accepts .tsv, .txt, .gz, or stdin (-).
The source is never changed. Output must not already exist. Each chunk has
the original comments/header and every original column. Unsorted input is OK.
Publish the whole output under a NEW S3 prefix, with manifest.json uploaded last.
"""
import argparse
from collections import OrderedDict, Counter
from contextlib import nullcontext
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

FACET_COLUMNS = (
    "assembly_level", "refseq_category", "version_status", "genome_rep",
    "assembly_type", "relation_to_type_material", "paired_asm_comp",
)


def open_input(source):
    if source == "-":
        return nullcontext(sys.stdin)
    opener = gzip.open if source.lower().endswith(".gz") else open
    return opener(source, "rt", encoding="utf-8-sig", newline="")


def prepare(source, destination, rows_per_chunk=20000,
            bytes_per_chunk=16 * 1024 * 1024, max_open_files=64):
    if min(rows_per_chunk, bytes_per_chunk, max_open_files) < 1:
        raise ValueError("Chunk limits and max-open-files must be positive")
    destination = Path(destination).expanduser().resolve()
    if destination.exists():
        raise ValueError("Output already exists; choose a new directory: %s" % destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".assembly-chunks-", dir=str(destination.parent)))
    handles = OrderedDict()
    groups = {}
    facets = {}
    header = None
    comments = []
    prefix = b""
    total_rows = 0

    def close_handles():
        while handles:
            _, handle = handles.popitem(last=False)
            handle.close()

    try:
        with open_input(source) as inp:
            for line_no, raw in enumerate(inp, 1):
                line = raw.rstrip("\r\n").lstrip("\ufeff")
                if not line:
                    continue
                if header is None:
                    candidate = line.lstrip("#").strip().split("\t")
                    if "organism_name" in candidate and "assembly_accession" in candidate:
                        header = [c.strip() for c in candidate]
                        if len(header) != len(set(header)):
                            raise ValueError("Duplicate column names in header")
                        org_index = header.index("organism_name")
                        # Normalize the header to standard NCBI #header syntax.
                        prefix = ("\n".join(comments + ["#" + "\t".join(header)]) + "\n").encode("utf-8")
                        facets = {c: Counter() for c in FACET_COLUMNS if c in header}
                        facet_indices = {c: header.index(c) for c in facets}
                    elif line.startswith("#"):
                        comments.append(line)
                    else:
                        raise ValueError("Line %d: expected a tab-separated header containing organism_name and assembly_accession" % line_no)
                    continue
                if line.startswith("#"):
                    raise ValueError("Line %d: unexpected comment/header after data began" % line_no)
                cells = line.split("\t")
                if len(cells) != len(header):
                    raise ValueError("Line %d: %d columns, expected %d (no rows were silently skipped)" % (line_no, len(cells), len(header)))
                words = cells[org_index].casefold().split()
                group_name = words[0] if words else ""
                group = groups.get(group_name)
                if group is None:
                    digest = hashlib.sha256(group_name.encode("utf-8")).hexdigest()
                    group = {"id": digest, "rows": 0, "chunks": []}
                    groups[group_name] = group
                data = (line + "\n").encode("utf-8")
                if len(prefix) + len(data) > bytes_per_chunk:
                    raise ValueError("Line %d: one row plus header exceeds --bytes-per-chunk" % line_no)
                chunk = group["chunks"][-1] if group["chunks"] else None
                if chunk is None or chunk["rows"] >= rows_per_chunk or chunk["bytes"] + len(data) > bytes_per_chunk:
                    if chunk is not None:
                        handle = handles.pop(chunk["key"], None)
                        if handle:
                            handle.close()
                    key = "data/%s/part-%06d.tsv" % (group["id"], len(group["chunks"]) + 1)
                    path = staging / key
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(prefix)
                    chunk = {"key": key, "rows": 0, "bytes": len(prefix)}
                    group["chunks"].append(chunk)
                key = chunk["key"]
                handle = handles.pop(key, None)
                if handle is None:
                    if len(handles) >= max_open_files:
                        _, oldest = handles.popitem(last=False)
                        oldest.close()
                    handle = (staging / key).open("ab")
                handles[key] = handle
                handle.write(data)
                chunk["rows"] += 1
                chunk["bytes"] += len(data)
                group["rows"] += 1
                total_rows += 1
                for column, idx in facet_indices.items():
                    # Cap facet cardinality; all underlying data remain intact.
                    value = cells[idx]
                    if value in facets[column] or len(facets[column]) < 100:
                        facets[column][value] += 1
                if total_rows % 100000 == 0:
                    print("Processed {:,} rows; {:,} organism-prefix groups".format(total_rows, len(groups)), file=sys.stderr, flush=True)
        close_handles()
        if header is None:
            raise ValueError("No NCBI-style header found")
        manifest = {
            "schema_version": 1,
            "dataset_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source": "stdin" if source == "-" else Path(source).name,
            "grouping": "first casefolded whitespace-delimited organism_name token",
            "columns": header,
            "comments": comments,
            "total_rows": total_rows,
            "rows_per_chunk": rows_per_chunk,
            "bytes_per_chunk": bytes_per_chunk,
            "facets": {c: sorted(values) for c, values in facets.items()},
            "groups": groups,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(str(staging), str(destination))
        print("Prepared {:,} rows in {:,} chunks across {:,} organism-prefix groups: {}".format(
            total_rows, sum(len(g["chunks"]) for g in groups.values()), len(groups), destination), file=sys.stderr)
        return manifest
    finally:
        close_handles()
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", "-i", required=True, help="NCBI TSV, .gz, or - for stdin")
    parser.add_argument("--outdir", "-o", required=True, help="New output directory (must not exist)")
    parser.add_argument("--rows-per-chunk", type=int, default=20000)
    parser.add_argument("--bytes-per-chunk", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-open-files", type=int, default=64)
    args = parser.parse_args()
    try:
        prepare(args.input, args.outdir, args.rows_per_chunk, args.bytes_per_chunk, args.max_open_files)
    except (OSError, ValueError) as exc:
        parser.exit(1, "ERROR: %s\n" % exc)


if __name__ == "__main__":
    main()
