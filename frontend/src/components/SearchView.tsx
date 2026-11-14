import { useMemo, useState } from "react";
import {
  HAS_BACKEND, classifyQuery, queryAssemblies, downloadTsv,
  QueryError, type QueryResult,
} from "../lib/assembly-config";
import { ResultsTable } from "./ResultsTable";
import {
  Search, Loader2, Download, AlertCircle, Dna, CheckCircle2, FileDown,
  Database,
} from "lucide-react";

type Phase = "idle" | "searching" | "done" | "error";

const EXAMPLES = ["Drosophila", "Drosophila melanogaster", "Homo sapiens", "Mus musculus"];

export function SearchView() {
  const [query, setQuery] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<{ msg: string; detail?: string } | null>(null);

  const kind = useMemo(() => classifyQuery(query), [query]);
  const canSearch =
    HAS_BACKEND && phase !== "searching" &&
    (kind === "genus" || kind === "species");

  const hint =
    kind === "invalid"
      ? "Use letters only — a genus (e.g. Drosophila) or genus + species (e.g. Drosophila melanogaster)."
      : kind === "genus"
        ? "Genus search — all species under this genus will be returned."
        : kind === "species"
          ? "Species search — only rows for this exact organism (and its strains) are returned."
          : "";

  async function runSearch() {
    if (!canSearch) return;
    setPhase("searching");
    setError(null);
    setResult(null);
    try {
      const r = await queryAssemblies(query);
      setResult(r);
      setPhase("done");
      // Immediately download to the user's computer, per the app's purpose.
      downloadTsv(r.tsv, r.filename);
    } catch (e: any) {
      if (e instanceof QueryError) {
        setError({ msg: e.message, detail: e.detail });
      } else {
        setError({ msg: String(e?.message ?? e) });
      }
      setPhase("error");
    }
  }

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Hero */}
      <section className="panel overflow-hidden bg-radial-glow">
        <div className="px-6 py-7">
          <div className="text-xs uppercase tracking-[0.2em] text-accent font-medium">
            assembly metadata
          </div>
          <h1 className="mt-1 text-2xl md:text-3xl font-semibold tracking-tight">
            Grab NCBI assembly records by organism
          </h1>
        </div>
      </section>

      {/* Search */}
      <section className="panel">
        <div className="panel-hd">
          <div className="flex items-center gap-2">
            <Dna size={14} className="text-accent" />
            <div>
              <h2 className="text-sm font-semibold">Organism name</h2>
              <p className="text-xs text-fg-muted">
                Matching is on the leading words of the{" "}
                <span className="font-mono">organism_name</span> column.
              </p>
            </div>
          </div>
        </div>
        <div className="panel-bd space-y-4">
          <div className="flex flex-col sm:flex-row gap-3">
            <input
              className="field flex-1"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") runSearch(); }}
              placeholder="e.g. Drosophila melanogaster"
              autoFocus
            />
            <button
              type="button"
              onClick={runSearch}
              disabled={!canSearch}
              className="btn-primary">
              {phase === "searching"
                ? (<><Loader2 size={16} className="animate-spin" /> Searching…</>)
                : (<><Search size={16} /> Search &amp; download</>)}
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-fg-subtle">Try:</span>
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => setQuery(ex)}
                className="pill border-border bg-surface-2 text-fg-muted hover:text-fg hover:border-accent/50 transition-colors font-mono">
                {ex}
              </button>
            ))}
          </div>

          {hint && (
            <p className={`text-[11px] leading-relaxed ${
              kind === "invalid" ? "text-warn" : "text-fg-subtle"
            }`}>
              {hint}
            </p>
          )}

          {!HAS_BACKEND && (
            <div className="text-fg-subtle text-[11px] leading-relaxed">
              Preview mode — no <code className="font-mono">VITE_AG_API_BASE</code>{" "}
              set. Search will fail until the env var is configured in Amplify.
            </div>
          )}
        </div>
      </section>

      {/* Error */}
      {phase === "error" && error && (
        <section className="panel border-danger/40">
          <div className="panel-bd flex items-start gap-3">
            <AlertCircle size={18} className="text-danger mt-0.5 shrink-0" />
            <div className="min-w-0">
              <div className="text-sm font-medium text-danger">{error.msg}</div>
              {error.detail && (
                <div className="text-xs text-fg-muted mt-1 leading-relaxed">
                  {error.detail}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Results */}
      {phase === "done" && result && (
        <section className="panel animate-fade-in">
          <div className="panel-hd flex-wrap gap-3">
            <div className="flex items-center gap-2">
              <CheckCircle2 size={16} className="text-accent" />
              <div>
                <h2 className="text-sm font-semibold">
                  {result.matchCount.toLocaleString()} assembl
                  {result.matchCount === 1 ? "y" : "ies"} matched
                </h2>
                <p className="text-xs text-fg-muted">
                  Saved as{" "}
                  <span className="font-mono">{result.filename}</span>
                  {result.totalRows !== null && (
                    <> · scanned {result.totalRows.toLocaleString()} rows</>
                  )}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => downloadTsv(result.tsv, result.filename)}
              className="btn-ghost ml-auto">
              <Download size={14} /> Download again
            </button>
          </div>
          <div className="panel-bd space-y-3">
            <div className="flex items-center gap-2 text-xs text-accent">
              <FileDown size={13} />
              Your download started automatically.
            </div>
            <ResultsTable parsed={result.parsed} />
          </div>
        </section>
      )}

      {/* Empty-state helper */}
      {phase === "idle" && (
        <section className="panel">
          <div className="panel-bd flex items-start gap-3 text-xs text-fg-muted">
            <Database size={16} className="text-fg-subtle mt-0.5 shrink-0" />
            <p className="leading-relaxed">
              The result TSV preserves every column from the NCBI
              assembly_summary table — accession, BioProject, taxid, assembly
              level, genome size, GC%, annotation counts, FTP path, and more —
              for just the organism you searched. Open it in Excel, pandas, or R.
            </p>
          </div>
        </section>
      )}
    </div>
  );
}
