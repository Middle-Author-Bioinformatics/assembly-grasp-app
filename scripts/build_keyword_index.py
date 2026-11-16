#!/usr/bin/env python3
"""Build an organism-name/row-pointer and optional BioProject SQLite index.

Python 3.8+; standard library only. Does not rewrite the manifest or chunks.
Upload the finished output under a NEW S3 prefix and configure Lambda's
ASSEMBLY_KEYWORD_INDEX_KEY with the key of organism_names.sqlite.
Enriched chunks automatically enable deduplicated BioProject title/description
search; the file name stays the same for compatibility.
"""
import argparse
from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import sys
import tempfile


def build(chunks_dir, outdir):
    root = Path(chunks_dir).expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    columns = manifest["columns"]
    if manifest.get("schema_version") != 1 or "organism_name" not in columns:
        raise ValueError("Unsupported chunk manifest")
    project_columns = [columns.index(c) for c in ("bioproject_title", "bioproject_description") if c in columns]
    outdir = Path(outdir).expanduser().resolve()
    if outdir.exists():
        raise ValueError("Output already exists; choose a new directory: %s" % outdir)
    outdir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".assembly-keywords-", dir=str(outdir.parent)))
    conn = None
    total, chunk_id = 0, 0
    try:
        database = staging / "organism_names.sqlite"
        conn = sqlite3.connect(str(database))
        conn.executescript("""
            PRAGMA page_size=8192;
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE chunks (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE);
            CREATE TABLE names (chunk_id INTEGER NOT NULL, row_no INTEGER NOT NULL,
                name TEXT NOT NULL, PRIMARY KEY(chunk_id, row_no)) WITHOUT ROWID;
        """)
        conn.executemany("INSERT INTO metadata VALUES (?, ?)", [
            ("schema_version", "1"), ("dataset_id", manifest["dataset_id"]),
            ("columns", json.dumps(columns)),
        ])
        modes = ["name", "keyword"]
        if project_columns:
            modes.append("bioproject")
            conn.executescript("""
                ALTER TABLE names ADD COLUMN project_id INTEGER;
                CREATE TABLE projects (id INTEGER PRIMARY KEY, text TEXT NOT NULL UNIQUE);
            """)
        conn.execute("INSERT INTO metadata VALUES ('search_modes', ?)", (json.dumps(modes),))

        @lru_cache(maxsize=32768)
        def project_id(text):
            conn.execute("INSERT OR IGNORE INTO projects (text) VALUES (?)", (text,))
            return conn.execute("SELECT id FROM projects WHERE text=?", (text,)).fetchone()[0]

        insert = "INSERT INTO names VALUES (?, ?, ?, ?)" if project_columns else "INSERT INTO names VALUES (?, ?, ?)"
        org_idx = columns.index("organism_name")
        batch = []
        for group in manifest["groups"].values():
            for chunk in group["chunks"]:
                key = chunk["key"]
                if PurePosixPath(key).is_absolute() or ".." in PurePosixPath(key).parts:
                    raise ValueError("Invalid chunk path")
                path = (root / key).resolve()
                if root not in path.parents:
                    raise ValueError("Chunk path escapes the input directory")
                chunk_id += 1
                conn.execute("INSERT INTO chunks VALUES (?, ?)", (chunk_id, key))
                row_no = 0
                with path.open(encoding="utf-8-sig") as stream:
                    for line in stream:
                        line = line.rstrip("\r\n")
                        if not line or line.startswith("#"):
                            continue
                        cells = line.split("\t")
                        if len(cells) != len(columns):
                            raise ValueError("Malformed data row in %s" % key)
                        name = " ".join(cells[org_idx].casefold().split())
                        row = (chunk_id, row_no, name)
                        if project_columns:
                            # A newline separates fields: phrases never match
                            # across the end of a title and start of a description.
                            text = "\n".join(" ".join(cells[i].casefold().split()) for i in project_columns)
                            row += (project_id(text),)
                        batch.append(row)
                        row_no += 1
                        total += 1
                        if len(batch) == 5000:
                            conn.executemany(insert, batch)
                            batch.clear()
                        if total % 100000 == 0:
                            print("Indexed {:,} organism names".format(total), file=sys.stderr, flush=True)
                if row_no != chunk["rows"]:
                    raise ValueError("Row count differs from manifest for %s" % key)
        if batch:
            conn.executemany(insert, batch)
        if total != manifest["total_rows"]:
            raise ValueError("Total row count differs from manifest")
        conn.execute("INSERT INTO metadata VALUES ('total_rows', ?)", (str(total),))
        if project_columns:
            conn.execute("CREATE INDEX names_by_project ON names(project_id, chunk_id, row_no)")
        project_id.cache_clear()
        conn.commit()
        conn.close()
        conn = None
        report = {"schema_version": 1, "dataset_id": manifest["dataset_id"],
                  "rows": total, "chunks": chunk_id, "index_file": database.name,
                  "index_bytes": database.stat().st_size,
                  "search_modes": modes,
                  "created_at": datetime.now(timezone.utc).isoformat()}
        (staging / "keyword_index.json").write_text(json.dumps(report, indent=2) + "\n")
        os.replace(str(staging), str(outdir))
        print("Prepared {:,} names; index {:.1f} MiB: {}".format(total, report["index_bytes"] / 1048576, outdir), file=sys.stderr)
        return report
    finally:
        if conn is not None:
            conn.close()
        if staging.exists():
            shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks-dir", required=True, help="Existing directory containing manifest.json and data/")
    parser.add_argument("--outdir", required=True, help="New output directory")
    args = parser.parse_args()
    try:
        build(args.chunks_dir, args.outdir)
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        parser.exit(1, "ERROR: %s\n" % exc)


if __name__ == "__main__":
    main()
