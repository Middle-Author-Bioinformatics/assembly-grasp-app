# Select references → build a GToTree report

This milestone connects the existing TaxonSlueth and GToTree deployments; no new Amplify app is needed. The source and report interfaces use the same MAB teal light/dark palette. Public genome selections persist in browser storage across searches, result pages, and refreshes. Exact assembly accessions are deduplicated, capped at 100, and retain their version suffixes and dataset provenance. Possible same-BioSample counterparts are flagged for review, not silently removed.

## Deploy

1. Merge/deploy the coordinated `gtotree-local` backend changes first (worker limits, reference metadata, updated report generator).
2. Deploy `gtotree-app` with `VITE_ASSEMBLY_GRASP_ORIGINS` set to the exact HTTPS origin of this app. Multiple custom/Amplify origins may be comma-separated. Do not use `*`.
3. Set `VITE_GTOTREE_APP_URL` in this existing TaxonSlueth Amplify app to the existing GToTree HTTPS URL and redeploy.

The floating **Selected genomes** button opens a separate cart drawer. The cart lists selected genomes with individual Remove buttons, Clear selection, Download selection JSON and Build tree. Closing it preserves the current search, page and filters. Escape closes the dialog and returns focus to the cart button.

The **Build tree** button in the cart opens GToTree, transfers the current selection, and lets the user add their own genomes before submission. The source, destination origin, window identity, and handshake nonce are checked. Genome metadata never appears in URLs. The `genome-selection.ts` v1 contract is mirrored in both repos and must stay compatible.

If a popup is blocked, opener isolation is enabled, or the app origins are not configured, **Download selection JSON** remains available; use **Import selection JSON** in GToTree. Selection data is a list of public assembly metadata, not an authorization claim. No account or project access is granted by importing it.

Only valid GCA/GCF records can be selected. Select-this-page is atomic: exceeding 100 produces a message and leaves the previous selection unchanged. Search/export filters do not clear the cart. The existing full-result exports remain independent of the tree-selection cap; users can still download every search match.

## Tests

```bash
cd frontend
npm install
npm run build
npm run test:exports
npm run test:selection
npm run test:handoff
npm run test:ui
```

UI tests render the actual React components in jsdom; they cover selection across pages, searches, and remounts. Handoff tests reject messages from the wrong origin, window, or nonce. jsdom tests check behavior, not pixel layout; review the Amplify branch preview before merging. No live backend is contacted during tests.

This phase preserves existing public result-code access. Shared accounts, private reports, revocable links, and stored inquiries are a separate cloud milestone; do not describe these changes as providing those features.
