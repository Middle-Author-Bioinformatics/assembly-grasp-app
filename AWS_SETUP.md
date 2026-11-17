# Assembly Grasp — indexed search setup

Run preprocessing on TheBelly, upload a new dataset prefix, deploy the updated
Lambda, then deploy the frontend. The original S3 object is never modified.
No database server, pandas, extra conda environment, or Python package is
needed for preprocessing; Python 3.8+ is sufficient.

For routine full refreshes after initial setup, use the automated workflow in
[REFRESH_DATA.md](REFRESH_DATA.md). It rebuilds assemblies, BioProject enrichment,
and keyword indexes together, with optional activation of a fresh S3 prefix.

## 1. Prepare the data on TheBelly

From the repository root (or use the downloaded standalone script):

```bash
python3 scripts/prepare_assembly_chunks.py \
  --input ncbi_assembly_info.tsv \
  --outdir ncbi_chunks_20261114_v1 \
  --rows-per-chunk 20000
```

Input can be `.tsv`, `.txt`, `.gz`, or `-` for stdin. The script detects the
header using `assembly_accession` and `organism_name`; column positions need
not match NCBI's usual order. Standard commented and unprefixed headers are
accepted. It preserves all data columns and rows, repeats comments/headers in
each chunk, handles interleaved groups, and limits open output files to 64.
Concatenated tables can repeat commented or unprefixed headers. If later
headers contain the same columns in a different order, their data are remapped
to the first header's column order. Later comments are preserved in the
manifest's `section_comments` field, with source line and row positions,
rather than repeated in every chunk. `source_header_count` records how many
source headers were accepted. All assembly rows, including duplicates, remain.
Default chunk limits are 20,000 rows or 16 MiB, whichever is reached first.
Adjust with `--rows-per-chunk`, `--bytes-per-chunk`, and `--max-open-files`.

The output directory must not already exist. Processing uses a temporary
sibling directory and publishes the completed directory only on success.
If a run ends with `ERROR`, its temporary chunks are removed and the requested
output directory is not published. After fixing the cause, rerun the command
from the beginning. Start the S3 upload only after the final `Prepared ...`
message and a completed `manifest.json` are present.
Malformed rows, duplicate columns, incompatible section schemas, or decoding
errors stop the build rather than silently dropping data. Reordered headers
are supported; headers with added or missing columns fail with the differences
listed. A single oversized
row requires a larger byte limit. Empty fields are retained. Missing organism
names are retained under an empty-name group, but cannot be found by a named
organism query. Grouping uses the leading name word, not a taxonomic hierarchy;
for example, names beginning `Candidatus` share that group.

To read directly from S3 without saving a local copy of the source:

```bash
set -o pipefail
aws s3 cp s3://assembly-grasp-bucket/ncbi_assembly_info.tsv - \
  --region us-east-2 | \
python3 scripts/prepare_assembly_chunks.py \
  --input - --outdir ncbi_chunks_20261114_v1
```

Run only one of the preceding alternatives. Output chunks still occupy local
disk. To preprocess without transferring the source to TheBelly, run the same
pipeline on AWS compute in the bucket's region instead. S3 itself does not run
the Python script or split the object through its console.

## 2. Upload a new immutable dataset prefix

Do not overwrite an existing index prefix while users are browsing it. For
each refresh, choose a fresh output directory and S3 prefix. Upload the
manifest last, after every chunk upload succeeds:

```bash
aws s3 sync ncbi_chunks_20261114_v1/ \
  s3://assembly-grasp-bucket/assembly-index/2026-11-14-v1/ \
  --exclude 'manifest.json' --region us-east-2
```

Only after the command above exits successfully:

```bash
aws s3 cp ncbi_chunks_20261114_v1/manifest.json \
  s3://assembly-grasp-bucket/assembly-index/2026-11-14-v1/manifest.json \
  --region us-east-2
```

The bucket stays private. The account used on TheBelly needs permission to read
the source (if streaming it) and upload objects to the new prefix.

## 3. Lambda code and permissions

Runtime: Python 3.12. Code: `lambda/assembly_grasp.py`. Handler:
`assembly_grasp.lambda_handler`. `boto3` is provided by the Lambda runtime.

If using the AWS CLI, create the ZIP from the repository root and replace
`YOUR_FUNCTION_NAME` with the existing function's actual name:

```bash
zip -j assembly_grasp.zip lambda/assembly_grasp.py
aws lambda update-function-code \
  --function-name YOUR_FUNCTION_NAME \
  --zip-file fileb://assembly_grasp.zip --region us-east-2
```

Alternatively paste the file in the Lambda editor, set the matching handler,
and deploy. In Configuration → Environment variables, add/update these values
while preserving any other environment variables:

| Variable | Value |
|---|---|
| `ASSEMBLY_BUCKET` | `assembly-grasp-bucket` |
| `ASSEMBLY_MANIFEST_KEY` | `assembly-index/2026-11-14-v1/manifest.json` |
| `AWS_REGION_NAME` | `us-east-2` |
| `MAX_RESULT_BYTES` | `5000000` (default) |

`ASSEMBLY_KEY=ncbi_assembly_info.tsv` is used only when the manifest variable is
empty. Missing or invalid configured manifests fail explicitly; the API does
not silently scan the full file instead. Removing the manifest variable
restores the full-file fallback. CORS no longer uses `ALLOWED_ORIGINS` in code.

The execution role needs CloudWatch logging permissions and `s3:GetObject` on
the new manifest/chunks, for example this additional policy statement:

```json
{
  "Effect": "Allow",
  "Action": "s3:GetObject",
  "Resource": "arn:aws:s3:::assembly-grasp-bucket/assembly-index/*"
}
```

Retain read permission on the original source if fallback mode is needed.
Lambda needs no S3 write, list, or delete permission for indexed searches.
Start by benchmarking 1,024 MB memory and a 120-second timeout. Actual settings
should reflect measured scan times for the largest commonly queried groups.

## 4. Function URL CORS and public invocation

Configure CORS exclusively on the Function URL, not in both code and URL
settings. In Configuration → Function URL → Edit:

| Setting | Value |
|---|---|
| Allowed origin | `https://main.d3p0n8syijshxf.amplifyapp.com` |
| Allowed methods | `GET`, `POST` |
| Allowed request headers | `content-type` |
| Exposed headers | `content-disposition`, `x-assembly-match-count`, `x-assembly-total-rows` |
| Max age | `600` |
| Allow credentials | Off |

Add any custom domain and local development origin separately, without
trailing slashes. AWS handles preflight OPTIONS responses. The handler does
not add duplicate CORS headers.

For the existing public application, retain Function URL auth type `NONE` and
its resource policy. If creating a new public Function URL with the CLI,
both `lambda:InvokeFunctionUrl` (condition `FunctionUrlAuthType=NONE`) and
`lambda:InvokeFunction` (condition `InvokedViaFunctionUrl=true`) need public
resource-policy permissions. The execution role's S3 policy alone does not
grant browser invocation permission.

## 5. Deploy the frontend after Lambda is ready

Keep Amplify build variable `VITE_AG_API_BASE` set to the updated Function URL.
Deploy the feature branch or merge the tested changes to the Amplify-connected
branch when ready. The repo's `amplify.yml` builds `frontend/` only. GitHub
frontend changes do not deploy Lambda or upload/preprocess the data.

Smoke test the API before updating the website:

```bash
curl -sS YOUR_FUNCTION_URL \
  -H 'Content-Type: application/json' \
  -d '{"query":"Geobacter","format":"json","page":1,"page_size":25}'
```

Check `indexed` is true, `columns` matches the source, totals look correct,
and page 2 continues with different records. Then test filters in the UI.

## Complete downloads from the dashboard

**Download all N matches** exports the complete active search, regardless of
the displayed page or the table's 25/50/100 setting. **Download this page (N)**
exports only the visible page. Both include every available column and full
BioProject text, independent of selected table columns.

The frontend retains a single TSV request for small exports. For likely large
exports, or after a full request returns 413/503/504, it fetches every JSON
page sequentially and combines them into one TSV with one header. An incomplete
successful TSV response is also detected and replaced by complete page
collection. Page size is reduced automatically if a wide JSON page exceeds
the response limit. The active query, search mode, filters, dataset UUID,
column schema, total count, and expected page length are preserved/validated;
a changed or failed request cannot produce a partial download. Duplicate
source assembly rows remain present.

Users see the completed/total record count and can cancel, including the
in-flight request. Keep the tab open until preparation finishes. This update
is frontend-only: merge/deploy through Amplify; the existing Lambda, indexes,
permissions, and environment variables do not need changes.

There is no fixed 100-row or 5-MB combined-export cap in the dashboard. The
browser assembles the final file, so its available memory remains a practical
limit for very large exports. Each API page still obeys the existing response
limit; one individual row must fit. Each page is an API request; very broad
filtered searches repeat the backend's exact-count scan and can take longer
and cost more than a small export. This is not a background export job and
does not write export objects to S3. Direct callers of `format: "tsv"` retain
the server response-size cap.

Run `npm run test:exports` from `frontend/` for automated download checks.

## Scope and performance

- A nonempty organism-name or keyword query is required. Filters apply across every
  match for that query before the page is selected, not just the visible rows.
- In Genus / species mode, unfiltered single-word queries use manifest counts to read only the chunk(s)
  needed for the requested page. Filters or species queries scan that entire
  group for exact totals on each request; large groups can still be slower.
- The complete match set is not sent to the browser at once. Every record is
  reachable through pages even when the aggregate result exceeds 5 MB.
- Direct API TSV responses remain bounded by `MAX_RESULT_BYTES`. The dashboard
  handles larger complete downloads by collecting JSON pages as described
  above. Raising the API cap above Lambda's buffered-response limits is not
  needed for the complete-download button.
- Column selection affects display only. Downloads include every source
  column, and no assemblies are deduplicated or quality-filtered implicitly.
- Each manifest has a dataset UUID. Page/download requests pin that UUID;
  after a dataset refresh, rerun the search if the API returns 409. Lambda
  caches the immutable manifest in warm instances. Never modify a published
  prefix in place; change the environment variable to a newly uploaded prefix.
- The tests use generated fixtures because no source TSV attachment was
  available during implementation. Validate one real dataset on TheBelly
  before publishing its index.

## Optional keyword search across organism names

`Genus / species` mode matches complete leading words in `organism_name`.
`Keyword` mode matches a literal substring or phrase anywhere in that same
column, ignoring capitalization and normalizing whitespace. For example,
`phage` can match `Escherichia phage ...` and `... bacteriophage ...` in
different prefix groups. It never searches other metadata columns implicitly.
This is text matching, not NCBI taxonomy/synonym resolution. The early `Group`
column shows the source classification; users can also filter it explicitly.

Keyword search needs a separate compact SQLite catalog of normalized organism
names and row pointers. Existing source chunks and manifest are left unchanged.
No new database service or Python dependencies are needed.

On TheBelly, from the repository root, build it from the existing local chunks:

```bash
python3 scripts/build_keyword_index.py \
  --chunks-dir ~/databases/ncbi_chunks_20261114_v1 \
  --outdir ~/databases/ncbi_keywords_20261115_v1
```

If using the standalone downloaded script in `~/databases`, replace
`scripts/build_keyword_index.py` with `build_keyword_index.py`. The output
directory must be new. The script validates the chunk row counts against the
manifest, preserves duplicate records, and publishes output only on success.

After the final `Prepared ...` message, upload the new keyword files:

```bash
aws s3 sync ~/databases/ncbi_keywords_20261115_v1/ \
  s3://assembly-grasp-bucket/assembly-index/2026-11-14-v1/keyword-v1/ \
  --region us-east-2
```

Deploy the updated `lambda/assembly_grasp.py` and add this environment variable
alongside the existing `ASSEMBLY_MANIFEST_KEY`:

```text
ASSEMBLY_KEYWORD_INDEX_KEY=assembly-index/2026-11-14-v1/keyword-v1/organism_names.sqlite
```

The existing read policy on `assembly-index/*` also covers the keyword file.
Keep every published keyword object immutable. A source dataset refresh needs
a newly built keyword index with that dataset's UUID and a new object key.
Mismatched datasets return 409 rather than silently returning wrong records.

The first keyword query in each Lambda execution environment downloads this
catalog into `/tmp`; subsequent requests in that environment reuse it. Check
`index_bytes` in the generated `keyword_index.json`. Configure temporary
(ephemeral) storage to exceed this size by at least 256 MiB; 2,048 MB is a
reasonable starting allocation for many catalogs. Start with a 120-second
timeout and benchmark a cold query on your actual data. A large catalog can
require more temporary storage or time. Memory is separate from temporary
storage; the SQLite page cache is bounded to about 32 MiB.

Unfiltered keyword pages scan the local name catalog for exact totals, then
retrieve only the assembly chunks containing the requested page's records.
Keyword queries with additional column filters read all name-matching records
to apply those filters before counting/paging, so very broad filtered searches
can still take longer. Direct TSV responses retain the configured size limit;
the dashboard automatically collects pages for larger complete downloads.

The schema API advertises `search_modes: ["name", "keyword"]` only when both
index environment variables are configured. Until then, or with an older
Lambda, the frontend keeps keyword mode disabled and scientific-name mode
usable. Deploy Lambda/configure the keyword index before deploying this UI.

Example request:

```json
{"query":"phage","search_mode":"keyword","format":"json","page":1,"page_size":50}
```

`search_mode` defaults to `name` for compatibility with existing clients.
The UI pins the chosen mode across page navigation and full TSV downloads.

## BioProject titles, descriptions, and keyword search

The optional enrichment adds `bioproject_title` and `bioproject_description`
to every assembly row. The dashboard shows them beside the BioProject link;
long descriptions expand with **Read more**. Page and full TSV downloads
include the full plain text. These fields also appear in the filter editor.
**BioProject text** mode searches a case-insensitive literal substring/phrase
in either field across all indexed assemblies. It does not require an organism
query. For example, search `antibiotic resistance`, then add an `organism_name`
filter to narrow the results. Alternatively, use Genus / species mode and add
`bioproject_description` → `contains` → `methicillin`.

This is project-level context, not sample-level metadata or confirmation that
every genome has a phenotype mentioned in the study. Missing/unavailable
projects or descriptions remain blank; no text is inferred from umbrella
projects. When a row lists multiple projects, values are labeled by accession.
XML-encoded HTML is converted to plain text and whitespace normalized. The
metadata is a snapshot, not a live lookup each time a user searches.

### Prepare on TheBelly

After merging/pulling this change, run these commands **from the repository
root**. If using the standalone scripts downloaded to `~/databases`, run there
and remove `scripts/` from each script path. The source chunk directory is the
one you already prepared; you do not need to split the original TSV again.

Download NCBI's bulk XML once. It is several GB (about 3.9 GB at implementation)
and is publicly available from the [BioProject FTP directory](https://ftp.ncbi.nlm.nih.gov/bioproject/).
The download command resumes an interrupted transfer. Do not use a previously
downloaded XML file as a resume target for a different NCBI snapshot.

```bash
curl -fL --retry 5 -C - \
  https://ftp.ncbi.nlm.nih.gov/bioproject/bioproject.xml \
  -o ~/databases/bioproject_20261116.xml
```

Once the download succeeds, prepare a **new** enriched dataset:

```bash
python3 scripts/enrich_bioprojects.py \
  --chunks-dir ~/databases/ncbi_chunks_20261114_v1 \
  --bioproject-xml ~/databases/bioproject_20261116.xml \
  --outdir ~/databases/ncbi_chunks_bioproject_20261116_v1
```

The script streams XML, builds a reusable SQLite cache beside it, then writes
a new copy of the chunks with two appended columns. Python 3.8+ and its standard
library are sufficient. Original XML, chunks, manifest, and assembly rows are
preserved. A new dataset UUID ensures browsers and Lambda cannot mix old/new
data. A failed preparation does not publish a partial output directory. The
local cache is reused if the XML path, size, and modification time match;
otherwise choose a new `--cache-db` path. Allow disk space for the XML, its
SQLite cache, enriched chunks, and keyword catalog. Enriched chunks can be
larger than the original splitter's byte limit because descriptions are added.

Review `bioproject_enrichment.json` for matched row counts and
`missing_bioprojects.txt` for project accessions absent from the XML snapshot.
An absent description is valid; it does not block publishing.

Build the updated keyword catalog **from the enriched chunks**, not the old
chunks. This version stores distinct project text once and links assembly row
pointers to it, alongside the existing organism-name catalog:

```bash
python3 scripts/build_keyword_index.py \
  --chunks-dir ~/databases/ncbi_chunks_bioproject_20261116_v1 \
  --outdir ~/databases/ncbi_keywords_bioproject_20261116_v1
```

### Upload and activate

Use a new S3 prefix. Run each command only after the previous one succeeds:

```bash
aws s3 sync ~/databases/ncbi_chunks_bioproject_20261116_v1/ \
  s3://assembly-grasp-bucket/assembly-index/2026-11-16-bioproject-v1/ \
  --exclude 'manifest.json' --region us-east-2

aws s3 sync ~/databases/ncbi_keywords_bioproject_20261116_v1/ \
  s3://assembly-grasp-bucket/assembly-index/2026-11-16-bioproject-v1/keyword-v1/ \
  --region us-east-2

aws s3 cp ~/databases/ncbi_chunks_bioproject_20261116_v1/manifest.json \
  s3://assembly-grasp-bucket/assembly-index/2026-11-16-bioproject-v1/manifest.json \
  --region us-east-2
```

Deploy the updated `lambda/assembly_grasp.py` separately from Amplify. Then
update these **two existing** Lambda environment variables together, preserving
all other variables:

```text
ASSEMBLY_MANIFEST_KEY=assembly-index/2026-11-16-bioproject-v1/manifest.json
ASSEMBLY_KEYWORD_INDEX_KEY=assembly-index/2026-11-16-bioproject-v1/keyword-v1/organism_names.sqlite
```

The existing `s3:GetObject` policy on `assembly-index/*` covers both. Check the
new catalog's `index_bytes` in `keyword_index.json` and size Lambda ephemeral
storage to fit it plus at least 256 MiB. The first keyword request per Lambda
environment downloads the catalog; warm requests reuse it. Benchmark a cold
query and a broad filtered query before rollout. Exact counts with additional
metadata filters can still require reading many matching chunks. Direct TSV
responses retain the size cap; the dashboard can collect larger exports from
JSON pages. No full XML is downloaded by Lambda.

Smoke tests (replace `YOUR_FUNCTION_URL`):

```bash
curl -sS YOUR_FUNCTION_URL -H 'Content-Type: application/json' \
  -d '{"operation":"schema"}'

curl -sS YOUR_FUNCTION_URL -H 'Content-Type: application/json' \
  -d '{"query":"methicillin","search_mode":"bioproject","format":"json","page_size":25}'
```

Confirm `columns` includes both new fields, schema `search_modes` contains
`bioproject`, and the result reports `search_mode: "bioproject"`. Check page 2,
an organism filter, and a TSV download. The UI disables BioProject mode until
the schema advertises it. Existing organism searches remain usable on the old
dataset. To roll back a dataset change, restore both old index keys together.
Keep all published prefixes immutable.

NCBI references: [bulk download](https://ftp.ncbi.nlm.nih.gov/bioproject/),
[BioProject XML schema](https://www.ncbi.nlm.nih.gov/data_specs/schema/other/bioproject/Core.xsd),
and [BioProject help](https://www.ncbi.nlm.nih.gov/books/NBK54015/).


