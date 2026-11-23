# Example bacterial taxa

Verified on 2026-11-22. These less commonly featured environmental bacterial genera were selected as manageable examples, not as a quantitative ranking of how much research each has received. All five return 10–100 assembly records in both NCBI Datasets and the deployed TaxonSlueth snapshot at verification.

| Genus | NCBI Datasets total_count | TaxonSlueth matches |
| --- | ---: | ---: |
| Desulfitobacterium | 55 | 53 |
| Pelobacter | 18 | 14 |
| Thermincola | 22 | 22 |
| Ferrimonas | 58 | 53 |
| Dehalogenimonas | 37 | 32 |

Counts are assembled genome records, not necessarily distinct isolates; GenBank/RefSeq pairs, taxonomy changes and dataset refreshes affect totals. These are dated verification counts, not permanent limits or live UI badges.

NCBI source: `https://api.ncbi.nlm.nih.gov/datasets/v2/genome/taxon/{genus}/dataset_report?page_size=1` (default filters), response `total_count`.

TaxonSlueth source: deployed search API, organism-name mode, no filters, first page, JSON response `total`. Dataset ID: `713b2462-e28d-402a-8b30-f00111e67ab5`. Its snapshot uses organism-name prefix matching rather than NCBI's taxonomic descendants, so counts can differ.

The buttons fill the query and switch to genus/species mode. They preserve filters and the genome selection and wait for the user to submit Search.
