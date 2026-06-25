# Assembly Grasp — AWS setup

End-to-end setup for the backend-less Assembly Grasp app: an S3 bucket holding
the metadata file, a Lambda (with a Function URL) that filters it, and an Amplify
site that calls the Lambda. Region used throughout: **us-east-2** (change to suit).

```
Browser ──POST {query}──► Lambda Function URL ──GetObject──► s3://assembly-grasp-bucket/ncbi_assembly_info.tsv
   ▲                                │
   └────── filtered TSV ◄───────────┘
```

---

## 1. S3 bucket + metadata file

1. Create a bucket named **`assembly-grasp-bucket`** in `us-east-2`
   (Block Public Access **on** — it can stay fully private).
2. Upload your metadata file to the bucket root as **`ncbi_assembly_info.tsv`**:

   ```bash
   aws s3 cp ncbi_assembly_info.tsv s3://assembly-grasp-bucket/ncbi_assembly_info.tsv
   ```

   The file is an NCBI `assembly_summary`-style TSV: optional `##` comment
   line(s), then a header line beginning `#assembly_accession\t…` that includes
   an `organism_name` column, then one assembly per row. The full NCBI file is
   available at
   `https://ftp.ncbi.nlm.nih.gov/genomes/genbank/assembly_summary_genbank.txt`.

> The bucket and key are overridable via the Lambda env vars `ASSEMBLY_BUCKET`
> and `ASSEMBLY_KEY` if you ever rename them.

---

## 2. Lambda function

Runtime: **Python 3.12**. Handler: **`assembly_grasp.lambda_handler`**.
Code: `lambda/assembly_grasp.py` (single file, only dependency is `boto3`, which
the Lambda runtime already bundles — no layer or packaging needed).

### Create it

```bash
cd lambda
zip assembly_grasp.zip assembly_grasp.py

aws lambda create-function \
  --function-name assembly-grasp \
  --runtime python3.12 \
  --handler assembly_grasp.lambda_handler \
  --timeout 60 \
  --memory-size 512 \
  --role arn:aws:iam::<ACCOUNT_ID>:role/<assembly-grasp-role> \
  --zip-file fileb://assembly_grasp.zip \
  --region us-east-2
```

Update later with:

```bash
aws lambda update-function-code \
  --function-name assembly-grasp \
  --zip-file fileb://assembly_grasp.zip --region us-east-2
```

### Execution-role IAM policy

The role needs only read access to the one object:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::assembly-grasp-bucket/ncbi_assembly_info.tsv"
    }
  ]
}
```

(Plus the standard `AWSLambdaBasicExecutionRole` for CloudWatch logs.)

### Environment variables (all optional — defaults shown)

| Variable | Default | Purpose |
| --- | --- | --- |
| `ASSEMBLY_BUCKET` | `assembly-grasp-bucket` | bucket to read |
| `ASSEMBLY_KEY` | `ncbi_assembly_info.tsv` | object key |
| `AWS_REGION_NAME` | `us-east-2` | region for the S3 client |
| `ALLOWED_ORIGINS` | *(empty = any)* | comma-separated web-origin allow-list |
| `MAX_RESULT_BYTES` | `5000000` | cap on response size (~5 MB) |

For production, set `ALLOWED_ORIGINS` to your real origins, e.g.
`https://main.dXXXX.amplifyapp.com,https://assembly.midauthorbio.com`.

### Sizing notes

* **Timeout / memory** — the Lambda streams the file line-by-line rather than
  loading it all, so memory stays low, but a multi-GB file takes time to scan.
  60 s / 512 MB is a safe start; raise the timeout if your file is very large.
* **Response size** — a Lambda Function URL buffered response is capped at ~6 MB.
  `MAX_RESULT_BYTES` (default 5 MB) guards against blowing past that: an
  over-large result returns HTTP 413 asking the user to narrow the search. A
  single genus of bacteria can be large — encourage genus + species for those.
  (If you need bigger downloads, switch the Function URL to **RESPONSE_STREAM**
  invoke mode, or have the Lambda write the TSV to S3 and return a presigned
  GET URL instead.)

### Function URL + CORS

Create a Function URL with **auth type `NONE`** (the data is public reference
metadata) and configure CORS:

```bash
aws lambda create-function-url-config \
  --function-name assembly-grasp \
  --auth-type NONE \
  --cors '{
    "AllowOrigins": ["*"],
    "AllowMethods": ["POST","GET"],
    "AllowHeaders": ["content-type"],
    "ExposeHeaders": ["content-disposition","x-assembly-match-count","x-assembly-total-rows"],
    "MaxAge": 600
  }' \
  --region us-east-2
```

Tighten `AllowOrigins` to your Amplify/custom domain for production. Note the
returned `FunctionUrl` — that is `VITE_AG_API_BASE`.

> The handler also sets CORS headers itself (driven by `ALLOWED_ORIGINS`), so set
> both consistently. Exposing `content-disposition` lets the browser read the
> download filename the Lambda chose.

### Smoke test

```bash
curl -s -X POST "<FunctionUrl>" \
  -H 'Content-Type: application/json' \
  -d '{"query":"Drosophila melanogaster"}' | head
```

You should get TSV back (comment + header + matching rows). A GET also works:
`curl -s "<FunctionUrl>?query=Drosophila%20melanogaster"`.

---

## 3. Amplify (frontend hosting)

1. Push this repo to GitHub/GitLab.
2. In the Amplify console → **New app → Host web app** → connect the repo.
3. **App root / monorepo**: set the build app root to **`frontend`** (the
   included `amplify.yml` already declares `appRoot: frontend`).
4. **Environment variables** (App settings → Environment variables):

   | Name | Value |
   | --- | --- |
   | `VITE_AG_API_BASE` | the Lambda **Function URL** from step 2 |
   | `VITE_AG_BUCKET` | `assembly-grasp-bucket` (display only) |

5. Deploy. Vite reads `VITE_*` vars at build time, so **re-deploy after changing
   them**.

If the env vars are missing the site still builds but runs in **preview mode**
(searches disabled with an explanatory banner).

---

## 4. Verify end-to-end

1. Open the Amplify URL.
2. Type `Drosophila melanogaster`, hit **Search & download**.
3. A `Drosophila_melanogaster.tsv` downloads, and a preview table + match count
   appear. Done.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Banner "Preview mode" | `VITE_AG_API_BASE` not set at build time → set it and redeploy |
| CORS error in console | Function URL CORS `AllowOrigins` / Lambda `ALLOWED_ORIGINS` don't include your site origin |
| HTTP 502 "could not read s3://…" | bucket/key wrong, or the execution role lacks `s3:GetObject` |
| HTTP 404 "no assemblies found" | no `organism_name` starts with the query (check spelling/capitalisation — matching is case-insensitive) |
| HTTP 413 "result too large" | genus too broad → search genus + species, or raise `MAX_RESULT_BYTES` / use response streaming |
