"""Assembly Grasp: chunk-aware filtering, JSON pages, and bounded TSV downloads.

ASSEMBLY_MANIFEST_KEY enables the index built by prepare_assembly_chunks.py.
Without it, the original ASSEMBLY_KEY is scanned (functional, but slower).
CORS is managed exclusively by the Lambda Function URL configuration.
"""
import base64
from contextlib import closing
from functools import lru_cache
import io
import json
import math
import os
from pathlib import PurePosixPath
import re

import boto3

ASSEMBLY_BUCKET = os.environ.get("ASSEMBLY_BUCKET", "assembly-grasp-bucket")
ASSEMBLY_KEY = os.environ.get("ASSEMBLY_KEY", "ncbi_assembly_info.tsv")
MANIFEST_KEY = os.environ.get("ASSEMBLY_MANIFEST_KEY", "").strip()
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
            yield line.rstrip("\r\n")


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


def _source(query):
    if MANIFEST_KEY:
        manifest = _manifest()
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
        meta, keys = _source(query)
        columns = meta["columns"]
        dataset_id = meta["dataset_id"]
        if body.get("dataset_id") and body["dataset_id"] != dataset_id:
            raise RequestError("Dataset changed. Run a new search.", 409)
        if schema_only:
            return _json(200, {"columns": columns, "facets": meta.get("facets", {}),
                               "dataset_id": dataset_id, "indexed": bool(MANIFEST_KEY)})
        rules = _filters(body.get("filters", []), columns)
        fmt = body.get("format", "tsv")
        if fmt not in ("json", "tsv"):
            raise RequestError("format must be json or tsv")
        page = _integer(body.get("page"), 1, 1, 100000000, "page")
        page_size = _integer(body.get("page_size"), 50, 1, 100, "page_size")
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
                first = True
                for line in lines:
                    if not line or line.startswith("#"):
                        continue
                    cells = line.split("\t")
                    if first and cells == columns:  # unprefixed input header
                        first = False
                        continue
                    first = False
                    if len(cells) != len(columns):
                        raise RuntimeError("Malformed row in assembly dataset")
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
