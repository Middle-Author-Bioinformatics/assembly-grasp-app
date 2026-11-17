# Refreshing the AssemblyGrasp dataset

Run `scripts/refresh_assemblies.py` on TheBelly whenever you want an updated
snapshot. It downloads the current NCBI GenBank and RefSeq assembly summaries,
rebuilds the prefix chunks, enriches them with fresh BioProject titles and
descriptions, and builds the matching organism/BioProject keyword catalog.
It downloads **metadata only**, not genome sequences.

The script uses Python 3.8+ and the standard library. Keep it beside the three
other scripts in the repository's `scripts/` directory. AWS CLI must be
installed and authenticated for upload and optional activation. The existing
Lambda must already have the chunk/BioProject-aware code deployed; this script
refreshes data and configuration, not application code.

## Run a refresh

After merging and pulling the change, run from your local repository root:

```bash
python3 scripts/refresh_assemblies.py \
  --workdir ~/databases/assembly-grasp-refreshes
```

This prepares and uploads a fresh dataset to
`s3://assembly-grasp-bucket/assembly-index/<UTC timestamp>-<unique suffix>/`.
It prints the two new index keys and the path to `refresh.json`, but does not
switch the live app. To also activate the completed refresh, use:

```bash
python3 scripts/refresh_assemblies.py \
  --workdir ~/databases/assembly-grasp-refreshes \
  --function-name YOUR_LAMBDA_FUNCTION_NAME \
  --activate
```

Replace `YOUR_LAMBDA_FUNCTION_NAME` with the actual name at the top of the AWS
Lambda console, **not** its Function URL. Repeat this same command for future
refreshes. No frontend rebuild is required after a dataset update. Users with
old results may need to run a new search after the dataset UUID changes.

For local preparation without any AWS operations:

```bash
python3 scripts/refresh_assemblies.py \
  --workdir ~/databases/assembly-grasp-refreshes \
  --prepare-only
```

## What happens during a run

1. A fresh timestamped local directory is created and locked against another
   refresh using the same work directory.
2. Current GenBank and RefSeq metadata are downloaded with retries, atomic file
   publication, size checks when provided, and SHA-256 provenance. Downloads
   restart rather than append bytes from potentially different NCBI snapshots.
3. Both tables are combined, accepting repeated/reordered headers. The existing
   splitter validates row widths and schemas; an empty dataset is rejected.
4. Fresh BioProject XML is downloaded and imported into a local SQLite cache.
   Project text is joined to the new chunks, and a new keyword catalog is built
   with the same dataset UUID and row counts.
5. Generated files are hashed. Upload/retry verifies those hashes first. Chunks
   are uploaded, then the keyword catalog, then the manifest **last**.
6. If `--activate` was supplied, the existing Lambda configuration is read.
   The script checks temporary storage, records the previous dataset keys,
   preserves unrelated environment variables, and updates the bucket and both
   index keys in one request guarded by the Lambda revision ID. It waits for
   the update and checks the resulting keys.

Current-source snapshots include changes to existing metadata as well as newly
added assemblies. Historical/replaced/suppressed report files are not included.
GenBank/RefSeq pairs are preserved as separate GCA/GCF records. This is a full
snapshot rebuild, not an append-only update or a deduplication step. Fresh
BioProject XML also makes new project text searchable. Downloads, source files,
local builds, and prior S3 versions are retained; nothing is automatically
deleted. Allow disk space for several GB of downloads plus caches, raw/enriched
chunks, and the keyword catalog per run.

## Retry publishing or activate later

If preparation succeeded but upload failed, or you chose `--prepare-only`, use
the exact refresh directory printed by that run:

```bash
python3 scripts/refresh_assemblies.py \
  --publish ~/databases/assembly-grasp-refreshes/RUN_DIRECTORY \
  --function-name YOUR_LAMBDA_FUNCTION_NAME \
  --activate
```

This reuses the prepared files and skips downloading/rebuilding. An already
uploaded run is not uploaded again. Preparation failures require a new refresh;
their report says `preparation_failed`, and partial preprocessing directories
are not publishable. If an activation request fails or times out, the report
stays `activation_requested`: check Lambda's current settings before assuming
which dataset is active. Retrying activation is idempotent for the same target.

`previous-dataset.json` contains only the old bucket/index variables and function
name, not unrelated environment variables or secrets. To roll back, restore
those values together in the Lambda console while retaining other variables.
If a previous value is null, remove that variable instead of entering `null`.
Old S3 prefixes remain available for rollback.

## Options and permissions

- `--assembly-source genbank` or `--assembly-source refseq`: refresh only that
  collection. Default is `both`.
- `--bioproject-xml /path/to/bioproject.xml`: explicitly reuse a local XML
  snapshot instead of downloading fresh project metadata.
- `--skip-bioprojects`: intentionally omit project text and BioProject keyword
  search from the new dataset. Existing source BioProject links remain.
- `--bucket`, `--prefix`, `--region`, `--profile`: override the bucket, prefix
  root, AWS region, or CLI profile. Defaults are `assembly-grasp-bucket`,
  `assembly-index`, and `us-east-2`. The saved region is reused by `--publish`.

The AWS identity running the script needs bucket listing and object upload
permissions under the selected prefix. With `--activate`, it also needs
`lambda:GetFunctionConfiguration` and `lambda:UpdateFunctionConfiguration` on
the existing function. The Lambda execution role still only needs its existing
read access to `assembly-index/*`; no write permission is added to the Lambda.

Activation checks that Lambda ephemeral storage can hold the new keyword
catalog plus 256 MiB headroom. Increase temporary storage in the Lambda console
if requested, then rerun `--publish ... --activate`; no rebuild is needed.
Local/source processing failures and upload failures cannot trigger a live
configuration update. A lock prevents overlapping runs on the same workdir.

Validation: `python3 -m unittest discover -s tests -v` from the repository root.
The tests use small source fixtures and mocked AWS operations; no full remote
dataset build or real Lambda activation is performed by the tests.

Sources: [NCBI assembly metadata downloads](https://www.ncbi.nlm.nih.gov/genome/doc/ftpfaq/#downloadservice),
[BioProject XML](https://ftp.ncbi.nlm.nih.gov/bioproject/),
[Lambda environment-variable updates](https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html).
