# Example bacterial taxa

Verified on 2026-11-22. The 24 shortcuts include environmental genera, noteworthy species and named strains. Geobacter is excluded. All selected genus and species queries return 10–100 records in both NCBI Datasets and the deployed TaxonSlueth snapshot. Strain queries deliberately return smaller focused sets and are exempt from that range.

| Rank | Search query | NCBI Datasets total_count | TaxonSlueth matches |
| --- | --- | ---: | ---: |
| Genera | Pelobacter | 18 | 14 |
| Genera | Thermincola | 22 | 22 |
| Genera | Dehalogenimonas | 37 | 32 |
| Genera | Desulfitobacterium | 55 | 53 |
| Genera | Ferrimonas | 58 | 53 |
| Genera | Desulfuromonas | 87 | 65 |
| Genera | Halorhodospira | 48 | 46 |
| Genera | Roseiflexus | 83 | 77 |
| Species | Deinococcus radiodurans | 39 | 39 |
| Species | Bdellovibrio bacteriovorus | 64 | 62 |
| Species | Myxococcus xanthus | 55 | 52 |
| Species | Azotobacter vinelandii | 25 | 25 |
| Species | Cupriavidus metallidurans | 65 | 53 |
| Species | Chloroflexus aurantiacus | 17 | 13 |
| Species | Rhodospirillum rubrum | 18 | 17 |
| Species | Mariprofundus ferrooxydans | 14 | 14 |
| Strains | Deinococcus radiodurans R1 | — | 24 |
| Strains | Bdellovibrio bacteriovorus HD100 | — | 2 |
| Strains | Myxococcus xanthus DK 1622 | — | 2 |
| Strains | Azotobacter vinelandii DJ | — | 8 |
| Strains | Cupriavidus metallidurans CH34 | — | 2 |
| Strains | Chloroflexus aurantiacus J-10-fl | — | 2 |
| Strains | Rhodospirillum rubrum ATCC 11170 | — | 2 |
| Strains | Mariprofundus ferrooxydans PV-1 | — | 4 |

Counts are dated assembly records, not necessarily distinct isolates. GenBank/RefSeq pairs, taxonomy updates and snapshot refreshes can affect totals. Counts are verification evidence rather than permanent limits or live UI badges.

NCBI source for genus/species totals: `https://api.ncbi.nlm.nih.gov/datasets/v2/genome/taxon/{name}/dataset_report?page_size=1` (URL-encoded name, default filters), response `total_count`.

TaxonSlueth source: deployed search API, organism-name mode, no filters, first page, JSON response `total`. Dataset ID: `713b2462-e28d-402a-8b30-f00111e67ab5`. Name-prefix matching differs from NCBI taxonomic-descendant queries, so totals can differ. Strain rows were checked directly against the app's NCBI-derived snapshot; their NCBI Datasets totals were not separately queried. Strain labels are searchable organism-name prefixes, not a guarantee of exhaustive strain metadata matching.

Buttons fill the query, select genus/species mode, preserve filters and the genome selection, and wait for Search to be submitted. Rank labels group shortcuts for browsing; all use the same organism-name search.
