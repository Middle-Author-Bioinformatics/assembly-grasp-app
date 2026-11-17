# Assembly Grasp

Search NCBI genome assembly metadata by organism, filter source columns, and
browse every matching record in pages. Genome sequences are not downloaded.

## What the app does

- Organism matching compares complete leading words, ignoring capitalization.
  `Geobacter` matches all names starting with that word; `Geobacter
  sulfurreducens` selects that species and its named strains.
- The search-mode toggle also offers keyword/phrase matching anywhere in
  `organism_name`, including viral names that contain a host genus. Keyword
  mode uses an optional SQLite name/row-pointer catalog built from existing
  chunks by `scripts/build_keyword_index.py`; see the keyword setup section
  of [AWS_SETUP.md](AWS_SETUP.md). It does not imply taxonomy-based matching.
- Any source column can be filtered with equality, inequality, text contains,
  numeric minimum/maximum, or missing/present checks. Conditions are combined
  with AND. Numeric comparisons exclude missing and non-finite values.
- Optional BioProject enrichment adds the study title and description beside
  each BioProject link. Descriptions expand in the table and remain complete
  in TSV downloads. **BioProject text** search finds a keyword/phrase in titles
  or descriptions across organisms, with the same filters and pagination.
  Build the enriched chunks with `scripts/enrich_bioprojects.py`, then build a
  new keyword catalog; see the BioProject section in [AWS_SETUP.md](AWS_SETUP.md).
  Metadata comes from an NCBI bulk XML snapshot, with no live per-query calls.
- Results use server-side pagination: 25, 50, or 100 records per page, with
  first/previous/next/last controls and page-number entry. Column visibility can
  be changed independently of filtering and downloads.
- `Group` is shown next to the organism name so source categories such as viral
  records are visible without scrolling to the end of the table.
- Download the current page, or download **all filtered matches** as one TSV,
  including every available column and complete BioProject text. The all-match
  download automatically collects every page when the full response is too
  large, with progress and cancellation. The table's 25/50/100 setting never
  limits the total download. Keep the tab open while it prepares the file.
- The source remains a snapshot supplied by the owner; no automatic NCBI
  refresh or BioSample/assembly deduplication is performed.

## Components

| Component | Location | Role |
|---|---|---|
| Frontend | `frontend/` | React, Vite, TypeScript, Tailwind; hosted on Amplify |
| Search API | `lambda/assembly_grasp.py` | S3-backed filtering and pagination via a Lambda Function URL |
| Preprocessor | `scripts/prepare_assembly_chunks.py` | Standard-library Python splitter for TheBelly or other Linux hosts |
| BioProject enrichment | `scripts/enrich_bioprojects.py` | Stream bulk NCBI XML and add project text to a new copy of the chunks |
| Keyword catalog | `scripts/build_keyword_index.py` | Index organism names and optional deduplicated project text |
| Tests | `tests/` | Offline integration checks with generated NCBI-style fixtures |

## Fast indexed searches

The preprocessor groups records by their first casefolded `organism_name`
word (usually genus) and subdivides large groups into bounded chunks. A
manifest records the schema, chunk paths, counts, and common categorical
values. The source does not need to be sorted.

In scientific-name mode, with `ASSEMBLY_MANIFEST_KEY` configured, Lambda reads only the requested group.
Unfiltered single-word searches can jump directly to a page using manifest row
counts. Filtered and species searches scan that group's chunks to compute exact
totals, so large groups can still take longer. No query cache is used.

See [AWS_SETUP.md](AWS_SETUP.md) for the splitter commands, upload order, Lambda
permissions/configuration, CORS, and deployment steps. Without a manifest,
the new API still works by scanning the original file, with slower searches.

## Local development and validation

```bash
cd frontend
cp .env.example .env.local
# Set VITE_AG_API_BASE to the updated Lambda Function URL in .env.local.
npm install
npm run dev
```

From the repository root:

```bash
python3 -m unittest discover -s tests -v
```

From `frontend/`, `npm run build` runs TypeScript checks and creates the static
production build. Amplify builds only the frontend; Lambda is deployed separately.
`npm run test:exports` checks complete downloads, including files larger than
5 MB, more than 100 records, preserved duplicates, cancellation, and dataset
consistency. The complete-download frontend update needs no Lambda or AWS
configuration changes; it uses the existing JSON pagination API.

## API examples

POST to the Function URL:

```json
{
  "query": "Geobacter",
  "format": "json",
  "filters": [
    {"column": "assembly_level", "op": "eq", "value": "Complete Genome"},
    {"column": "genome_size", "op": "gte", "value": "3000000"}
  ],
  "page": 1,
  "page_size": 50
}
```

Use actual column names/units from the source. `operation: "schema"` returns
available columns and common categorical suggestions. JSON pages return
`columns`, `rows`, `total`, `page`, `page_size`, `pages`, `dataset_id`, `indexed`,
and `scanned`. Send the returned `dataset_id` on subsequent pages/downloads;
409 means the dataset changed and a new search is required. `format: "tsv"`
returns all filtered matches up to `MAX_RESULT_BYTES`. Omitting `format` retains
the original TSV API, including GET `?query=Geobacter`.

© 2026 Middle Author Bioinformatics · For research and educational use only.

