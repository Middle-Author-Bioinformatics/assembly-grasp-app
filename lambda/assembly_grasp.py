"""assembly-grasp — Lambda Function URL that extracts assembly metadata rows.

Assembly Grasp is a backend-less web app: a React SPA on Amplify talks directly
to THIS Lambda (via its Function URL), and there is no server in between.

What it does
------------
The Lambda reads a single tab-separated metadata file out of S3 —

    s3://<ASSEMBLY_BUCKET>/<ASSEMBLY_KEY>     (default: ncbi_assembly_info.tsv)

— which is an NCBI `assembly_summary`-style table. The user types a *genus* (e.g.
"Drosophila") or a *genus + species* (e.g. "Drosophila melanogaster"); the Lambda
streams the file line-by-line, keeps the comment/header lines, and emits every
data row whose `organism_name` column begins with the requested name. The matching
rows are returned as a TSV, which the browser downloads immediately.

Endpoint contract (single route, POST):
    POST  {"query": "Drosophila melanogaster"}
    ->    200  text/tab-separated-values   (the filtered TSV, with header)
          headers:
            Content-Disposition: attachment; filename="Drosophila_melanogaster.tsv"
            X-Assembly-Match-Count: <number of data rows matched>
            X-Assembly-Total-Rows:  <number of data rows scanned>

A GET request with a `?query=...` string is also accepted, so the URL can be
shared / bookmarked.

Why stream instead of read-all: a full NCBI assembly_summary file can be 1-2 GB.
We never hold the whole thing in memory — we iterate the S3 streaming body one
line at a time and only accumulate the (much smaller) matched rows.

Matching rule
-------------
The query is lower-cased and split into whitespace tokens. A row matches when the
leading tokens of its `organism_name` equal the query tokens:

    query "Drosophila"               -> matches "Drosophila melanogaster",
                                                 "Drosophila pseudoobscura ...", ...
    query "Drosophila melanogaster"  -> matches "Drosophila melanogaster" (and its
                                                 strains/subspecies) but NOT
                                                 "Drosophila pseudoobscura"

This cleanly covers both "genus" and "genus + species" without matching a genus
whose name merely starts with the same letters.

Environment variables
---------------------
    ASSEMBLY_BUCKET        (optional)  S3 bucket. Default "assembly-grasp-bucket".
    ASSEMBLY_KEY           (optional)  Object key. Default "ncbi_assembly_info.tsv".
    AWS_REGION_NAME        (optional)  Default "us-east-2".
    ALLOWED_ORIGINS        (optional)  Comma-separated allow-list of web origins,
                                       e.g. "https://main.dXXXX.amplifyapp.com,
                                             https://assembly.midauthorbio.com".
                                       Empty = reflect any Origin (dev only).
    MAX_RESULT_BYTES       (optional)  Safety cap on the response body size in
                                       bytes. Default 5_000_000 (~5 MB, under the
                                       Lambda Function URL buffered-response limit).
                                       If a result would exceed this, the Lambda
                                       returns HTTP 413 with a JSON error so the
                                       frontend can ask the user to narrow down.

IAM: the execution role needs s3:GetObject on
     arn:aws:s3:::<ASSEMBLY_BUCKET>/<ASSEMBLY_KEY>.
"""
import io
import json
import os
import re

import boto3

ASSEMBLY_BUCKET = os.environ.get("ASSEMBLY_BUCKET", "assembly-grasp-bucket")
ASSEMBLY_KEY = os.environ.get("ASSEMBLY_KEY", "ncbi_assembly_info.tsv")
REGION = os.environ.get("AWS_REGION_NAME", "us-east-2")
MAX_RESULT_BYTES = int(os.environ.get("MAX_RESULT_BYTES", str(5_000_000)))
ALLOWED_ORIGINS = set(
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()
)

# The organism name lives in the `organism_name` column of the NCBI
# assembly_summary table. We resolve its index from the header row at runtime so
# the code keeps working if NCBI reorders columns, but fall back to the known
# position (8th column, 0-based index 7) if the header can't be parsed.
ORGANISM_COL_NAME = "organism_name"
ORGANISM_COL_FALLBACK = 7

# Used to build a safe download filename from the user's query.
_FNAME_CLEAN = re.compile(r"[^A-Za-z0-9._-]+")

s3 = boto3.client("s3", region_name=REGION)


def _cors(origin):
    """Reflect an allowed Origin (or any Origin when the allow-list is empty)."""
    if origin and (not ALLOWED_ORIGINS or origin in ALLOWED_ORIGINS):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Expose-Headers":
                "Content-Disposition, X-Assembly-Match-Count, X-Assembly-Total-Rows",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        }
    return {}


def _json(status, headers, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **headers},
        "body": json.dumps(body),
    }


def _safe_filename(query):
    base = _FNAME_CLEAN.sub("_", query.strip()).strip("_") or "assemblies"
    return f"{base}.tsv"


def _extract_query(event, method):
    """Pull the query string from a GET ?query= or a POST JSON body."""
    if method == "GET":
        params = event.get("queryStringParameters") or {}
        return str(params.get("query") or params.get("q") or "").strip()

    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return None  # signal "bad body"
    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    return str(body.get("query") or body.get("q") or "").strip()


def _iter_lines(stream):
    """Yield decoded text lines from a boto3 StreamingBody without loading it all."""
    buffered = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
    for line in buffered:
        yield line.rstrip("\n").rstrip("\r")


def _organism_index(header_line):
    cols = header_line.lstrip("#").split("\t")
    try:
        return cols.index(ORGANISM_COL_NAME)
    except ValueError:
        return ORGANISM_COL_FALLBACK


def _matches(organism_name, q_tokens):
    org_tokens = organism_name.lower().split()
    return org_tokens[: len(q_tokens)] == q_tokens


def lambda_handler(event, _context):
    headers_in = event.get("headers") or {}
    origin = headers_in.get("origin") or headers_in.get("Origin")
    method = (
        (event.get("requestContext", {}) or {})
        .get("http", {})
        .get("method", "POST")
    )
    cors = _cors(origin)

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": cors, "body": ""}
    if method not in ("POST", "GET"):
        return _json(405, cors, {"error": f"method {method} not allowed"})

    query = _extract_query(event, method)
    if query is None:
        return _json(400, cors, {"error": "invalid request body"})
    if not query:
        return _json(400, cors, {"error": "missing 'query' (a genus or genus + species name)"})
    if len(query) > 200:
        return _json(400, cors, {"error": "query too long"})

    q_tokens = query.lower().split()

    # Stream the metadata file from S3 and filter row-by-row.
    try:
        obj = s3.get_object(Bucket=ASSEMBLY_BUCKET, Key=ASSEMBLY_KEY)
    except Exception as e:  # noqa: BLE001 — surface the S3 error to the client
        print(json.dumps({"msg": "s3_get_failed", "bucket": ASSEMBLY_BUCKET,
                          "key": ASSEMBLY_KEY, "error": str(e)}))
        return _json(502, cors, {
            "error": f"could not read s3://{ASSEMBLY_BUCKET}/{ASSEMBLY_KEY}",
            "detail": str(e),
        })

    out = io.StringIO()
    out_bytes = 0
    org_idx = ORGANISM_COL_FALLBACK
    header_seen = False
    match_count = 0
    total_rows = 0
    truncated = False

    for line in _iter_lines(obj["Body"]):
        if not line:
            continue
        # Comment lines: the leading "##" description plus the "#<col>..." header.
        if line.startswith("#"):
            if not header_seen and ORGANISM_COL_NAME in line:
                org_idx = _organism_index(line)
                header_seen = True
            out.write(line + "\n")
            out_bytes += len(line) + 1
            continue

        total_rows += 1
        cols = line.split("\t")
        if org_idx >= len(cols):
            continue
        if _matches(cols[org_idx], q_tokens):
            row = line + "\n"
            enc_len = len(row.encode("utf-8"))
            if out_bytes + enc_len > MAX_RESULT_BYTES:
                truncated = True
                break
            out.write(row)
            out_bytes += enc_len
            match_count += 1

    print(json.dumps({
        "msg": "query_done", "query": query, "matches": match_count,
        "scanned": total_rows, "truncated": truncated,
    }))

    if truncated:
        return _json(413, cors, {
            "error": "result too large",
            "detail": (
                f"More than ~{MAX_RESULT_BYTES // 1_000_000} MB of matching rows for "
                f"{query!r}. Narrow the search by adding a species name."
            ),
            "matches_so_far": match_count,
        })

    if match_count == 0:
        return _json(404, cors, {
            "error": "no assemblies found",
            "detail": f"No rows whose organism_name starts with {query!r}.",
            "scanned": total_rows,
        })

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/tab-separated-values; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{_safe_filename(query)}"',
            "X-Assembly-Match-Count": str(match_count),
            "X-Assembly-Total-Rows": str(total_rows),
            **cors,
        },
        "body": out.getvalue(),
    }
