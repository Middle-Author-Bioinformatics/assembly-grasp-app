"""Assembly Grasp: chunk-aware filtering, JSON pages, and bounded TSV downloads.

ASSEMBLY_MANIFEST_KEY enables the index built by prepare_assembly_chunks.py.
Without it, the original ASSEMBLY_KEY is scanned (functional, but slower).
ASSEMBLY_KEYWORD_INDEX_KEY optionally points to a SQLite name/row-pointer
catalog built from the same manifest by build_keyword_index.py.
CORS is managed exclusively by the Lambda Function URL configuration.
"""
import base64
import hashlib
from contextlib import closing
from functools import lru_cache
import io
from itertools import groupby
import json
import math
import os
from pathlib import PurePosixPath
import re
import shutil
import sqlite3
import tempfile

import boto3

ASSEMBLY_BUCKET = os.environ.get("ASSEMBLY_BUCKET", "assembly-grasp-bucket")
ASSEMBLY_KEY = os.environ.get("ASSEMBLY_KEY", "ncbi_assembly_info.tsv")
MANIFEST_KEY = os.environ.get("ASSEMBLY_MANIFEST_KEY", "").strip()
KEYWORD_INDEX_KEY = os.environ.get("ASSEMBLY_KEYWORD_INDEX_KEY", "").strip()
REGION = os.environ.get("AWS_REGION_NAME", "us-east-2")
MAX_RESULT_BYTES = int(os.environ.get("MAX_RESULT_BYTES", "5000000"))
s3 = boto3.client("s3", region_name=REGION)
OPERATORS = {"eq", "ne", "contains", "gte", "lte", "missing", "present"}
MISSING = {"", "na", "n/a", "nan", "none", "null", "-"}


class RequestError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _json(status, body):
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        status = 413
        encoded = json.dumps({"error": "Page too large. Choose a smaller page size."})
    return {"statusCode": status, "headers": {
        "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store",
    }, "body": encoded}


def _lines(key, etag=None):
    args = {"Bucket": ASSEMBLY_BUCKET, "Key": key}
    if etag:
        args["IfMatch"] = etag
    obj = s3.get_object(**args)
    with io.TextIOWrapper(obj["Body"], encoding="utf-8-sig", errors="strict", newline="") as stream:
        for line in stream:
            yield line.rstrip("\r\n").lstrip("\ufeff")


@lru_cache(maxsize=1)
def _manifest():
    # Index prefixes are immutable: upload a fresh prefix for each refresh.
    obj = s3.get_object(Bucket=ASSEMBLY_BUCKET, Key=MANIFEST_KEY)
    with closing(obj["Body"]) as body:
        manifest = json.load(body)
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Unsupported assembly manifest version")
    if "organism_name" not in manifest.get("columns", []):
        raise RuntimeError("Manifest has no organism_name column")
    return manifest


def _source(query, search_mode="name"):
    if MANIFEST_KEY:
        manifest = _manifest()
        if search_mode == "keyword":
            return manifest, []
        words = query.casefold().split()
        group = manifest["groups"].get(words[0]) if words else None
        root = str(PurePosixPath(MANIFEST_KEY).parent)
        root = "" if root == "." else root + "/"
        keys = []
        for chunk in (group or {}).get("chunks", []):
            key = chunk["key"]
            if key.startswith("/") or ".." in PurePosixPath(key).parts:
                raise RuntimeError("Invalid key in assembly manifest")
            keys.append(root + key)
        return manifest, keys
    # A metadata-only request reads just the source header and closes the body.
    obj = s3.get_object(Bucket=ASSEMBLY_BUCKET, Key=ASSEMBLY_KEY)
    comments = []
    columns = None
    with io.TextIOWrapper(obj["Body"], encoding="utf-8-sig", errors="strict", newline="") as stream:
        for raw in stream:
            line = raw.rstrip("\r\n")
            candidate = line.lstrip("#").strip().split("\t")
            if "organism_name" in candidate and "assembly_accession" in candidate:
                columns = [c.strip() for c in candidate]
                break
            if line.startswith("#"):
                comments.append(line)
            elif line:
                break
    if columns is None:
        raise RuntimeError("Source TSV has no recognizable assembly header")
    return {"columns": columns, "comments": comments, "facets": {},
            "dataset_id": obj.get("ETag", "unindexed"), "total_rows": None}, [ASSEMBLY_KEY]


def _check_time(context):
    if context and context.get_remaining_time_in_millis() < 2000:
        raise RequestError("Search needs more time. Increase the Lambda timeout or narrow the search.", 503)


def _keyword_database(meta, context):
    if not MANIFEST_KEY or not KEYWORD_INDEX_KEY:
        raise RequestError("Keyword search is not enabled for this dataset yet.", 501)
    fingerprint = hashlib.sha256((KEYWORD_INDEX_KEY + meta["dataset_id"]).encode()).hexdigest()[:24]
    path = os.path.join(tempfile.gettempdir(), "assembly-keywords-" + fingerprint + ".sqlite")
    if not os.path.exists(path):
        obj = s3.get_object(Bucket=ASSEMBLY_BUCKET, Key=KEYWORD_INDEX_KEY)
        partial = path + ".partial"
        try:
            with closing(obj["Body"]) as stream:
                size = obj.get("ContentLength", 0)
                if size > shutil.disk_usage(tempfile.gettempdir()).free - 16 * 1048576:
                    raise RequestError("Keyword index does not fit in Lambda temporary storage. Increase ephemeral storage.", 503)
                with open(partial, "wb") as output:
                    while True:
                        _check_time(context)
                        block = stream.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
            os.replace(partial, path)
        finally:
            if os.path.exists(partial):
                os.unlink(partial)
    conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA cache_size=-32768")
        metadata = dict(conn.execute("SELECT key, value FROM metadata"))
        if metadata.get("schema_version") != "1" or metadata.get("dataset_id") != meta["dataset_id"] or json.loads(metadata.get("columns", "null")) != meta["columns"]:
            raise RequestError("Keyword index does not match this dataset. Rebuild the keyword index.", 409)
        if context:
            conn.set_progress_handler(lambda: int(context.get_remaining_time_in_millis() < 2000), 10000)
        return conn
    except Exception:
        conn.close()
        raise


def _keyword_rows(pointers, columns, context, stats):
    root = str(PurePosixPath(MANIFEST_KEY).parent)
    root = "" if root == "." else root + "/"
    for relative, group in groupby(pointers, lambda row: row[0]):
        _check_time(context)
        if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise RuntimeError("Invalid keyword index chunk key")
        wanted = {row[1] for row in group}
        last = max(wanted)
        row_no = 0
        stats["chunks"] += 1
        with closing(_lines(root + relative)) as lines:
            for line in lines:
                if not line or line.startswith("#"):
                    continue
                stats["scanned"] += 1
                if row_no % 4096 == 0:
                    _check_time(context)
                if row_no in wanted:
                    cells = line.split("\t")
                    if len(cells) != len(columns):
                        raise RuntimeError("Malformed row in assembly chunk")
                    wanted.remove(row_no)
                    yield cells, line
                if row_no >= last:
                    break
                row_no += 1
        if wanted:
            raise RuntimeError("Keyword index row pointer does not match the chunk")


def _keyword_search(meta, query, rules, fmt, page, page_size, context):
    rows = []
    stats = {"chunks": 0, "scanned": 0}
    start, end = (page - 1) * page_size, page * page_size
    prefix = "\n".join(meta.get("comments", []) + ["#" + "\t".join(meta["columns"])]) + "\n"
    output = io.StringIO() if fmt == "tsv" else None
    out_bytes = len(prefix.encode("utf-8"))
    if output is not None:
        output.write(prefix)
    needle = " ".join(query.casefold().split())
    try:
        with closing(_keyword_database(meta, context)) as conn:
            sql = "SELECT c.key, n.row_no FROM names n JOIN chunks c ON n.chunk_id=c.id WHERE instr(n.name, ?) > 0 ORDER BY n.chunk_id, n.row_no"
            if not rules and fmt == "json":
                total = conn.execute("SELECT count(*) FROM names WHERE instr(name, ?) > 0", (needle,)).fetchone()[0]
                pointers = conn.execute(sql + " LIMIT ? OFFSET ?", (needle, page_size, start))
                with closing(_keyword_rows(pointers, meta["columns"], context, stats)) as selected:
                    rows = [cells for cells, _ in selected]
            else:
                total = 0
                pointers = conn.execute(sql, (needle,))
                with closing(_keyword_rows(pointers, meta["columns"], context, stats)) as selected:
                    for cells, line in selected:
                        if not _passes(cells, rules):
                            continue
                        if output is not None:
                            out_bytes += len((line + "\n").encode("utf-8"))
                            if out_bytes > MAX_RESULT_BYTES:
                                raise RequestError("Full TSV exceeds the download limit. Add filters or download individual pages.", 413)
                            output.write(line + "\n")
                        elif start <= total < end:
                            rows.append(cells)
                        total += 1
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc).lower():
            raise RequestError("Keyword search needs more time. Increase the Lambda timeout or narrow the search.", 503) from exc
        raise
    print(json.dumps({"msg": "query_done", "search_mode": "keyword", "query": query, "matches": total, **stats}))
    if fmt == "json":
        return _json(200, {"columns": meta["columns"], "rows": rows, "total": total,
                           "page": page, "page_size": page_size,
                           "pages": max(1, (total + page_size - 1) // page_size),
                           "dataset_id": meta["dataset_id"], "indexed": True,
                           "search_mode": "keyword", "scanned": stats["scanned"]})
    if not total:
        return _json(404, {"error": "No assemblies match this search and filters"})
    filename = (re.sub(r"[^A-Za-z0-9._-]+", "_", query).strip("_") or "assemblies") + ".tsv"
    return {"statusCode": 200, "headers": {
        "Content-Type": "text/tab-separated-values; charset=utf-8",
        "Content-Disposition": 'attachment; filename="%s"' % filename,
        "Cache-Control": "no-store", "X-Assembly-Match-Count": str(total),
        "X-Assembly-Total-Rows": str(stats["scanned"]),
    }, "body": output.getvalue()}


def _number(raw):
    try:
        value = float(str(raw).strip())
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _filters(raw, columns):
    if not isinstance(raw, list) or len(raw) > 20:
        raise RequestError("filters must be an array with at most 20 conditions")
    compiled = []
    for rule in raw:
        if not isinstance(rule, dict):
            raise RequestError("Each filter must be an object")
        column, op = rule.get("column"), rule.get("op")
        if not isinstance(column, str) or not isinstance(op, str) or column not in columns or op not in OPERATORS:
            raise RequestError("Unknown filter column or operator")
        value = str(rule.get("value", "")).strip()
        if len(value) > 500:
            raise RequestError("Filter value too long")
        if op in ("gte", "lte"):
            value = _number(value)
            if value is None:
                raise RequestError("Numeric filters require a finite number")
        elif op not in ("missing", "present") and not value:
            raise RequestError("Enter a filter value or use 'is missing'")
        else:
            value = value.casefold()
        compiled.append((columns.index(column), op, value))
    return compiled


def _passes(cells, rules):
    for idx, op, expected in rules:
        value = cells[idx].strip().casefold()
        if op == "missing":
            ok = value in MISSING
        elif op == "present":
            ok = value not in MISSING
        elif op in ("gte", "lte"):
            number = _number(value)
            ok = number is not None and (number >= expected if op == "gte" else number <= expected)
        elif op == "eq":
            ok = value == expected
        elif op == "ne":
            ok = value != expected
        else:
            ok = expected in value
        if not ok:
            return False
    return True


def _integer(raw, default, low, high, name):
    if raw is None:
        return default
    if isinstance(raw, bool) or len(str(raw)) > 10 or not re.fullmatch(r"[0-9]+", str(raw)):
        raise RequestError(name + " must be an integer")
    value = int(raw)
    if not low <= value <= high:
        raise RequestError("%s must be between %d and %d" % (name, low, high))
    return value


def _request(event, method):
    if method == "GET":
        body = dict(event.get("queryStringParameters") or {})
        if "filters" in body:
            body["filters"] = json.loads(body["filters"])
        return body
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw, validate=True).decode("utf-8")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise RequestError("Request body must be a JSON object")
    return body


def _indexed_unfiltered_page(meta, keys, query, page, page_size):
    """Use manifest row counts to jump straight to relevant chunks."""
    group = meta["groups"].get(query.casefold().split()[0], {"rows": 0, "chunks": []})
    total = group["rows"]
    skip = (page - 1) * page_size
    rows, scanned = [], 0
    for key, chunk in zip(keys, group["chunks"]):
        if skip >= chunk["rows"]:
            skip -= chunk["rows"]
            continue
        with closing(_lines(key)) as lines:
            for line in lines:
                if not line or line.startswith("#"):
                    continue
                scanned += 1
                if skip:
                    skip -= 1
                    continue
                cells = line.split("\t")
                if len(cells) != len(meta["columns"]):
                    raise RuntimeError("Malformed row in assembly chunk")
                rows.append(cells)
                if len(rows) == page_size:
                    break
        if len(rows) == page_size:
            break
    print(json.dumps({"msg": "query_done", "query": query, "matches": total,
                      "scanned": scanned, "page": page, "indexed": True, "format": "json"}))
    return _json(200, {"columns": meta["columns"], "rows": rows, "total": total,
                       "page": page, "page_size": page_size,
                       "pages": max(1, (total + page_size - 1) // page_size),
                       "dataset_id": meta["dataset_id"], "indexed": True, "scanned": scanned})


def lambda_handler(event, context):
    method = ((event.get("requestContext") or {}).get("http") or {}).get("method", "POST").upper()
    if method == "OPTIONS":
        return {"statusCode": 204, "body": ""}
    if method not in ("GET", "POST"):
        return _json(405, {"error": "Method not allowed"})
    try:
        try:
            body = _request(event, method)
        except (ValueError, UnicodeError, TypeError) as exc:
            raise RequestError("Invalid request body") from exc
        query = " ".join(str(body.get("query") or body.get("q") or "").split())
        schema_only = body.get("operation") == "schema"
        if not schema_only and not query:
            raise RequestError("Enter a genus or an organism name")
        if len(query) > 200:
            raise RequestError("Query too long")
        search_mode = body.get("search_mode", "name")
        if search_mode not in ("name", "keyword"):
            raise RequestError("search_mode must be name or keyword")
        if not schema_only and search_mode == "keyword" and (not MANIFEST_KEY or not KEYWORD_INDEX_KEY):
            raise RequestError("Keyword search is not enabled for this dataset yet.", 501)
        meta, keys = _source(query, search_mode)
        columns = meta["columns"]
        dataset_id = meta["dataset_id"]
        if body.get("dataset_id") and body["dataset_id"] != dataset_id:
            raise RequestError("Dataset changed. Run a new search.", 409)
        if schema_only:
            return _json(200, {"columns": columns, "facets": meta.get("facets", {}),
                               "dataset_id": dataset_id, "indexed": bool(MANIFEST_KEY),
                               "search_modes": ["name", "keyword"] if MANIFEST_KEY and KEYWORD_INDEX_KEY else ["name"]})
        rules = _filters(body.get("filters", []), columns)
        fmt = body.get("format", "tsv")
        if fmt not in ("json", "tsv"):
            raise RequestError("format must be json or tsv")
        page = _integer(body.get("page"), 1, 1, 100000000, "page")
        page_size = _integer(body.get("page_size"), 50, 1, 100, "page_size")
        if search_mode == "keyword":
            return _keyword_search(meta, query, rules, fmt, page, page_size, context)
        start, end = (page - 1) * page_size, page * page_size
        org_idx = columns.index("organism_name")
        q_words = query.casefold().split()
        if MANIFEST_KEY and fmt == "json" and not rules and len(q_words) == 1:
            return _indexed_unfiltered_page(meta, keys, query, page, page_size)
        rows = []
        total = 0
        scanned = 0
        prefix = "\n".join(meta.get("comments", []) + ["#" + "\t".join(columns)]) + "\n"
        out = io.StringIO() if fmt == "tsv" else None
        if out is not None:
            out.write(prefix)
        out_bytes = len(prefix.encode("utf-8"))
        for key in keys:
            if context and context.get_remaining_time_in_millis() < 2000:
                raise RequestError("Search needs more time. Enable the chunk index or increase the Lambda timeout.", 503)
            # closing also closes the S3 body if a result-size limit interrupts iteration.
            with closing(_lines(key, None if MANIFEST_KEY else dataset_id)) as lines:
                identity_order = list(range(len(columns)))
                input_order = identity_order
                for line in lines:
                    if not line:
                        continue
                    cells = line.split("\t")
                    if line.startswith("#"):
                        candidate = [c.strip() for c in line.lstrip("#").strip().split("\t")]
                        if "assembly_accession" not in candidate and "organism_name" not in candidate:
                            continue
                    elif "assembly_accession" in cells and "organism_name" in cells:
                        candidate = [c.strip() for c in cells]
                    else:
                        candidate = None
                    if candidate is not None:
                        if len(candidate) != len(set(candidate)) or set(candidate) != set(columns):
                            raise RuntimeError("Incompatible assembly header in dataset")
                        input_order = [candidate.index(c) for c in columns]
                        continue
                    if len(cells) != len(columns):
                        raise RuntimeError("Malformed row in assembly dataset")
                    if input_order != identity_order:
                        cells = [cells[i] for i in input_order]
                        line = "\t".join(cells)
                    scanned += 1
                    if scanned % 4096 == 0 and context and context.get_remaining_time_in_millis() < 2000:
                        raise RequestError("Search needs more time. Enable the chunk index or increase the Lambda timeout.", 503)
                    if cells[org_idx].casefold().split()[:len(q_words)] != q_words or not _passes(cells, rules):
                        continue
                    if out is not None:
                        row = line + "\n"
                        out_bytes += len(row.encode("utf-8"))
                        if out_bytes > MAX_RESULT_BYTES:
                            raise RequestError("Full TSV exceeds the download limit. Add filters or download individual pages. All matches remain available in the paginated viewer.", 413)
                        out.write(row)
                    elif start <= total < end:
                        rows.append(cells)
                    total += 1
        print(json.dumps({"msg": "query_done", "query": query, "matches": total,
                          "scanned": scanned, "chunks": len(keys), "page": page,
                          "indexed": bool(MANIFEST_KEY), "format": fmt}))
        if fmt == "json":
            return _json(200, {"columns": columns, "rows": rows, "total": total,
                               "page": page, "page_size": page_size,
                               "pages": max(1, (total + page_size - 1) // page_size),
                               "dataset_id": dataset_id, "indexed": bool(MANIFEST_KEY),
                               "scanned": scanned})
        if not total:
            return _json(404, {"error": "No assemblies match this search and filters"})
        filename = (re.sub(r"[^A-Za-z0-9._-]+", "_", query).strip("_") or "assemblies") + ".tsv"
        return {"statusCode": 200, "headers": {
            "Content-Type": "text/tab-separated-values; charset=utf-8",
            "Content-Disposition": 'attachment; filename="%s"' % filename,
            "Cache-Control": "no-store", "X-Assembly-Match-Count": str(total),
            "X-Assembly-Total-Rows": str(scanned),
        }, "body": out.getvalue()}
    except RequestError as exc:
        return _json(exc.status, {"error": str(exc)})
    except Exception:
        # Do not expose internal S3/IAM details in public API errors.
        import traceback
        traceback.print_exc()
        return _json(502, {"error": "Could not read the assembly dataset. Check the Lambda logs and dataset configuration."})


