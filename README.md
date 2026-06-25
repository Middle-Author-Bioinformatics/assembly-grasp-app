# Assembly Grasp

Extract NCBI genome-assembly metadata by organism. Type a **genus** (e.g.
`Drosophila`) or a **genus + species** (e.g. `Drosophila melanogaster`); the app
returns every matching row from an NCBI `assembly_summary`-style table as a TSV
and downloads it straight to your computer.

There is **no backend server**. The whole thing is:

```
React SPA (AWS Amplify) ──POST {query}──► Lambda (Function URL) ──reads──► S3
        ▲                                         │                  ncbi_assembly_info.tsv
        └──────────── filtered TSV (download) ◄───┘
```

* **Frontend** — a Vite + React + Tailwind SPA in `frontend/`, hosted on Amplify.
  Same visual style/theme as the breseq web server.
* **Lambda** — `lambda/assembly_grasp.py`, exposed via a Lambda **Function URL**.
  It streams `ncbi_assembly_info.tsv` out of the S3 bucket
  `assembly-grasp-bucket`, keeps the rows whose `organism_name` column starts
  with the query, and returns them as `text/tab-separated-values`.
* **S3** — the bucket `assembly-grasp-bucket` holds the single metadata file the
  Lambda reads. The bucket itself does **not** need to be public.

## How matching works

The query is lower-cased and split into words. A row matches when the leading
words of its `organism_name` equal the query words:

| query | matches |
| --- | --- |
| `Drosophila` | `Drosophila melanogaster`, `Drosophila pseudoobscura …`, … |
| `Drosophila melanogaster` | `Drosophila melanogaster` and its strains/subspecies — **not** `Drosophila pseudoobscura` |

This covers both "genus" and "genus + species" cleanly, without matching a
different genus that merely shares a prefix.

## Repository layout

```
assembly-grasp-app/
├── amplify.yml                 # Amplify monorepo build spec (appRoot: frontend)
├── AWS_SETUP.md                # step-by-step S3 + Lambda + Amplify setup
├── README.md
├── frontend/                   # Vite + React + Tailwind SPA
│   ├── .env.example            # VITE_AG_API_BASE, VITE_AG_BUCKET
│   └── src/
│       ├── App.tsx
│       ├── components/         # Header, SearchView, ResultsTable
│       └── lib/                # assembly-config.ts (Lambda client), theme, utils
└── lambda/
    └── assembly_grasp.py       # Function-URL handler: stream + filter + return TSV
```

## Local development

```bash
cd frontend
cp .env.example .env.local      # point VITE_AG_API_BASE at your deployed Lambda
npm install
npm run dev                     # http://localhost:5173
```

Without `VITE_AG_API_BASE` the app loads in **preview mode**: the UI works but
searches fail until the env var is set.

## Deploying

See **[AWS_SETUP.md](./AWS_SETUP.md)** for the full walkthrough: create the
bucket, upload the metadata file, deploy the Lambda + Function URL, wire CORS,
and connect the repo to Amplify with the two build-time env vars.

---

© 2026 Middle Author Bioinformatics · For research and educational use only.
