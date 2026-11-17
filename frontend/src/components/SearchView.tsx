import { useEffect, useRef, useState } from "react";
import type { ExportProgress } from "../lib/export-pages";
import { HAS_BACKEND, queryAssemblies, loadSchema, downloadAll, downloadPage,
  type AssemblyFilter, type AssemblySchema, type QueryResult, type SearchRequest, type SearchMode } from "../lib/assembly-config";
import { ResultsTable } from "./ResultsTable";
import { FilterEditor } from "./FilterEditor";
import { Search, Loader2, Download, AlertCircle, Dna } from "lucide-react";
const EXAMPLES = ["Geobacter", "Geobacter sulfurreducens", "Drosophila melanogaster", "Homo sapiens"];

export function SearchView() {
  const [query, setQuery] = useState("");
  const [searchMode, setSearchMode] = useState<SearchMode>("name");
  const [filters, setFilters] = useState<AssemblyFilter[]>([]);
  const [pageSize, setPageSize] = useState(50);
  const [schema, setSchema] = useState<AssemblySchema | null>(null);
  const [schemaError, setSchemaError] = useState("");
  const [schemaRetry, setSchemaRetry] = useState(0);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [active, setActive] = useState<SearchRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState<ExportProgress | null>(null);
  const [exportMessage, setExportMessage] = useState("");
  const exportController = useRef<AbortController | null>(null);
  const [error, setError] = useState("");
  const [pageInput, setPageInput] = useState("1");
  useEffect(() => () => exportController.current?.abort(), []);
  useEffect(() => {
    if (!HAS_BACKEND) return;
    let cancelled = false;
    setSchemaError("");
    loadSchema().then(s => { if (!cancelled) setSchema(s); }).catch(e => {
      if (!cancelled) setSchemaError(e.message || String(e));
    });
    return () => { cancelled = true; };
  }, [schemaRetry]);
  const keywordAvailable = schema?.search_modes?.includes("keyword") ?? false;
  const bioprojectAvailable = schema?.search_modes?.includes("bioproject") ?? false;
  const modeAvailable = searchMode === "name" || (searchMode === "keyword" ? keywordAvailable : bioprojectAvailable);
  const invalidFilter = filters.some(f => f.op !== "missing" && f.op !== "present" && !f.value.trim());
  const canSearch = HAS_BACKEND && !!schema && !busy && !exporting && !invalidFilter &&
    modeAvailable &&
    !!query.trim() && query.length <= 200 && !/[\t\r\n]/.test(query);
  async function execute(search: SearchRequest) {
    setBusy(true); setError(""); setExportMessage("");
    try {
      const next = await queryAssemblies(search);
      setResult(next); setActive({ ...search, dataset_id: next.dataset_id }); setPageInput(String(next.page));
    } catch (e: any) { setError(e.message || String(e)); }
    finally { setBusy(false); }
  }
  function newSearch() {
    if (canSearch) execute({ query: query.trim(), search_mode: searchMode, filters: filters.map(f => ({ ...f })), page: 1, page_size: pageSize });
  }
  function go(page: number) {
    if (active && result && !busy && !exporting && Number.isInteger(page) && page >= 1 && page <= result.pages) execute({ ...active, page });
  }
  async function exportAll() {
    if (!active || !result || exporting || busy) return;
    const controller = new AbortController();
    exportController.current = controller;
    setExporting(true); setError(""); setExportMessage("");
    setExportProgress({ completed: 0, total: result.total });
    try {
      await downloadAll(active, result, { signal: controller.signal, onProgress: setExportProgress });
      setExportMessage(`Prepared all ${result.total.toLocaleString()} matches for download.`);
    } catch (e: any) {
      if (controller.signal.aborted) setExportMessage("Download cancelled. No file was saved.");
      else setError(e.message || String(e));
    } finally {
      exportController.current = null;
      setExporting(false); setExportProgress(null);
    }
  }
  function pageNavigation(position: "top" | "bottom") {
    if (!result) return null;
    return (
      <nav aria-label={`Result pages (${position})`} className="flex flex-wrap items-center gap-2 text-xs">
        <button className="btn-ghost" disabled={busy || exporting || result.page <= 1} onClick={() => go(1)}>First</button>
        <button className="btn-ghost" disabled={busy || exporting || result.page <= 1} onClick={() => go(result.page - 1)}>Previous</button>
        <span>Page {result.page.toLocaleString()} of {result.pages.toLocaleString()}</span>
        <button className="btn-ghost" disabled={busy || exporting || result.page >= result.pages} onClick={() => go(result.page + 1)}>Next</button>
        <button className="btn-ghost" disabled={busy || exporting || result.page >= result.pages} onClick={() => go(result.pages)}>Last</button>
        <label className="ml-auto flex items-center gap-2">Go to page <input aria-label={`Go to page (${position})`} className="field w-24" type="number" min={1} max={result.pages} value={pageInput}
          disabled={busy || exporting} onChange={e => setPageInput(e.target.value)} onKeyDown={e => { if (e.key === "Enter") go(Number(pageInput)); }} /></label>
        <button className="btn-ghost" disabled={busy || exporting || !/^\d+$/.test(pageInput) || Number(pageInput) < 1 || Number(pageInput) > result.pages} onClick={() => go(Number(pageInput))}>Go</button>
      </nav>
    );
  }
  return <div className="space-y-6 animate-fade-in">
    <section className="panel overflow-hidden bg-radial-glow"><div className="px-6 py-7">
      <h1 className="text-2xl md:text-3xl font-semibold tracking-tight">Grab NCBI assembly records by organism</h1>
      <p className="mt-3 max-w-3xl text-sm text-fg-muted leading-relaxed">Search NCBI assembly metadata by organism or BioProject text, filter the results, and browse or download matching records.</p>
    </div></section>
    <section className="panel">
      <div className="panel-hd flex items-center gap-2"><Dna size={14} className="text-accent" /><h2 className="text-sm font-semibold">Find assemblies</h2></div>
      <div className="panel-bd space-y-4">
        <fieldset className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm" disabled={busy || exporting}>
          <legend className="mb-2 text-xs text-fg-muted">Search mode</legend>
          <label className="inline-flex items-center gap-2 cursor-pointer"><input type="radio" name="search-mode" value="name" checked={searchMode === "name"} onChange={() => setSearchMode("name")} /> Genus / species</label>
          <label className={`inline-flex items-center gap-2 ${keywordAvailable ? "cursor-pointer" : "opacity-50"}`}><input type="radio" name="search-mode" value="keyword" disabled={!keywordAvailable} checked={searchMode === "keyword"} onChange={() => setSearchMode("keyword")} /> Organism keyword</label>
          <label className={`inline-flex items-center gap-2 ${bioprojectAvailable ? "cursor-pointer" : "opacity-50"}`}><input type="radio" name="search-mode" value="bioproject" disabled={!bioprojectAvailable} checked={searchMode === "bioproject"} onChange={() => setSearchMode("bioproject")} /> BioProject text</label>
          {schema && !keywordAvailable && <span className="text-xs text-fg-subtle">Keyword search is not available for this dataset yet.</span>}
          {schema && !bioprojectAvailable && <span className="text-xs text-fg-subtle">BioProject text search is not available for this dataset yet.</span>}
        </fieldset>
        <div className="flex flex-col sm:flex-row gap-3">
          <input aria-label={searchMode === "bioproject" ? "BioProject title or description keyword" : "Organism name"} className="field flex-1" value={query} disabled={busy || exporting}
            onChange={e => setQuery(e.target.value)} onKeyDown={e => { if (e.key === "Enter") newSearch(); }} placeholder={searchMode === "bioproject" ? "e.g. antibiotic resistance" : searchMode === "keyword" ? "e.g. phage or virus" : "e.g. Geobacter sulfurreducens"} autoFocus />
          <button type="button" className="btn-primary" disabled={!canSearch} onClick={newSearch}>
            {busy ? <><Loader2 size={16} className="animate-spin" /> Searching…</> : <><Search size={16} /> Search &amp; view</>}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs"><span className="text-fg-subtle">Try:</span>
          {(searchMode === "bioproject" ? ["antibiotic resistance", "soil", "microbiome", "clinical"] : searchMode === "keyword" ? ["phage", "virus", "uncultured", "sulfurreducens"] : EXAMPLES).map(ex => <button key={ex} disabled={busy || exporting} className="pill border-border bg-surface-2 text-fg-muted font-mono" onClick={() => setQuery(ex)}>{ex}</button>)}
        </div>
        <p className="text-[11px] text-fg-subtle">{searchMode === "bioproject" ? "Find a keyword or exact phrase in BioProject titles or descriptions, ignoring capitalization. Add organism_name filters to narrow by organism." : searchMode === "keyword" ? "Find a keyword or phrase anywhere in organism_name, ignoring capitalization." : "Match complete genus/species words at the start of organism_name, ignoring capitalization."}</p>
        {schema?.columns.includes("bioproject_description") && <p className="text-[11px] text-fg-subtle">BioProject text describes the study and may be unavailable for some records.</p>}
        {schema && <FilterEditor schema={schema} filters={filters} onChange={setFilters} disabled={busy || exporting} />}
        {HAS_BACKEND && !schema && !schemaError && <p className="text-xs text-fg-muted" role="status">Loading filter columns…</p>}
        {schemaError && <div className="text-xs text-danger" role="alert">{schemaError} <button className="btn-ghost" onClick={() => setSchemaRetry(x => x + 1)}>Retry</button></div>}
        {!HAS_BACKEND && <p className="text-xs text-fg-muted">Preview mode — search service is not configured.</p>}
        <label className="flex items-center gap-3 text-xs text-fg-muted">Results per page
          <select aria-label="Results per page" className="field w-24" value={pageSize} disabled={busy || exporting} onChange={e => {
            const size = Number(e.target.value); setPageSize(size);
            if (active) execute({ ...active, page: 1, page_size: size });
          }}>{[25, 50, 100].map(n => <option key={n} value={n}>{n}</option>)}</select>
        </label>
      </div>
    </section>
    {error && <section className="panel border-danger/40" role="alert"><div className="panel-bd flex gap-3 text-sm text-danger"><AlertCircle size={18} className="shrink-0" />{error}</div></section>}
    {result && active && <section className="panel" aria-busy={busy}>
      <div className="panel-hd flex flex-wrap items-center gap-3">
        <div className="mr-auto"><h2 className="text-sm font-semibold" aria-live="polite">{result.total.toLocaleString()} assemblies matched</h2>
          <p className="text-xs text-fg-muted">Results for <span className="italic">{active.query}</span> · {active.search_mode === "bioproject" ? "BioProject text" : active.search_mode === "keyword" ? "organism keyword" : "genus/species"} search · {active.filters.length} active filter{active.filters.length === 1 ? "" : "s"}</p>
        </div>
        <button className="btn-ghost" disabled={busy || exporting || result.rows.length === 0} onClick={() => downloadPage(result, active.query)}><Download size={14} /> Download this page ({result.rows.length.toLocaleString()})</button>
        <button className="btn-ghost" disabled={busy || exporting || result.total === 0} onClick={exportAll}>{exporting ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />} {exporting ? "Preparing download…" : `Download all ${result.total.toLocaleString()} matches`}</button>
      </div>
      <div className="panel-bd space-y-4">
        {exporting && exportProgress && <div className="space-y-2 text-xs" role="status" aria-live="polite">
          <div className="flex flex-wrap items-center gap-3"><span>Preparing {exportProgress.completed.toLocaleString()} of {exportProgress.total.toLocaleString()} matches. Keep this tab open.</span>
            <button className="btn-ghost" onClick={() => exportController.current?.abort()}>Cancel download</button></div>
          <progress className="w-full" aria-label="Download preparation" value={exportProgress.completed} max={Math.max(1, exportProgress.total)} />
        </div>}
        {exportMessage && <p className="text-xs text-fg-muted" role="status">{exportMessage}</p>}
        {result.total === 0 ? <p className="text-sm text-fg-muted">No assemblies match this search and its filters.</p> : <>
          <div className="text-xs text-fg-muted" role="status">{busy ? "Loading results…" : `Showing ${((result.page - 1) * result.page_size + 1).toLocaleString()}–${Math.min(result.page * result.page_size, result.total).toLocaleString()} of ${result.total.toLocaleString()}`}</div>
          {pageNavigation("top")}
          <ResultsTable result={result} />
          {pageNavigation("bottom")}
        </>}
        <p className="text-[11px] text-fg-subtle">Results per page controls the table only. Download all includes every matching record across all pages, every available column, and full BioProject text when present.</p>
      </div>
    </section>}
  </div>;
}

