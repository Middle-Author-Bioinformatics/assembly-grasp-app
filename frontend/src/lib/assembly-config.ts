import { collectAllPages, isCompleteTsv, type ExportOptions, type ExportSnapshot } from "./export-pages";
const env = import.meta.env;
export const AG_CONFIG = {
  apiBase: (env.VITE_AG_API_BASE as string | undefined)?.replace(/\/+$/, "") || "",
  bucket: (env.VITE_AG_BUCKET as string | undefined) || "assembly-grasp-bucket",
};
export const HAS_BACKEND = AG_CONFIG.apiBase !== "";
export type SearchMode = "name" | "keyword" | "bioproject";
export type FilterOp = "eq" | "ne" | "contains" | "gte" | "lte" | "missing" | "present";
export interface AssemblyFilter { column: string; op: FilterOp; value: string }
export interface AssemblySchema {
  columns: string[]; facets: Record<string, string[]>; dataset_id: string; indexed: boolean;
  search_modes?: SearchMode[];
}
export interface QueryResult {
  columns: string[]; rows: string[][]; total: number; page: number;
  page_size: number; pages: number; dataset_id: string; indexed: boolean; scanned: number;
  search_mode?: SearchMode;
}
export interface SearchRequest {
  query: string; search_mode: SearchMode; filters: AssemblyFilter[]; page: number; page_size: number; dataset_id?: string;
}
export class QueryError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
async function request(payload: object, signal?: AbortSignal): Promise<Response> {
  if (!HAS_BACKEND) throw new QueryError("No search service configured.", 0);
  let res: Response;
  try {
    res = await fetch(AG_CONFIG.apiBase, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal });
  } catch (error) {
    if (signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
    throw new QueryError("Could not reach the search service. Please try again.", 0);
  }
  if (!res.ok) {
    let message = `Request failed (HTTP ${res.status})`;
    try { const body = await res.json(); message = body.error || message; } catch { /* non-JSON error */ }
    throw new QueryError(message, res.status);
  }
  return res;
}
export async function loadSchema(): Promise<AssemblySchema> {
  const res = await request({ operation: "schema" });
  const schema = await res.json();
  if (!Array.isArray(schema.columns)) throw new QueryError("Update the search service to enable filters and pages.", 0);
  return schema;
}
export async function queryAssemblies(search: SearchRequest, signal?: AbortSignal): Promise<QueryResult> {
  const res = await request({ ...search, format: "json" }, signal);
  if (!res.headers.get("Content-Type")?.includes("application/json")) {
    throw new QueryError("Update the search service to enable filters and pages.", 0);
  }
  const result = await res.json();
  if (!Array.isArray(result.rows) || !Array.isArray(result.columns) || !Number.isInteger(result.total)) {
    throw new QueryError("Unexpected search response. Please try again.", 0);
  }
  if (search.search_mode !== "name" && result.search_mode !== search.search_mode) {
    throw new QueryError("Update the search service before using keyword search.", 0);
  }
  return result;
}
export function downloadFilename(raw: string): string {
  return `${raw.trim().replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "assemblies"}.tsv`;
}
export function downloadTsv(tsv: string, filename: string): void {
  downloadBlob(new Blob([tsv], { type: "text/tab-separated-values;charset=utf-8" }), filename);
}
function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function downloadAll(search: SearchRequest, snapshot: ExportSnapshot, options: ExportOptions = {}): Promise<void> {
  if (!snapshot.dataset_id || !Number.isSafeInteger(snapshot.total) || snapshot.total < 0 || !snapshot.columns.length) {
    throw new QueryError("Run a new search before downloading all matches.", 0);
  }
  if (options.signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
  const pinned = { ...search, dataset_id: snapshot.dataset_id };
  options.onProgress?.({ completed: 0, total: snapshot.total });
  // The single-response path remains quick for smaller exports. A 413 switches
  // automatically to complete paginated collection instead of limiting rows.
  const sample = snapshot.rows ?? [];
  const encoder = new TextEncoder();
  const estimatedBytes = sample.length ? sample.reduce((sum, row) => sum + encoder.encode(row.join("\t") + "\n").length, 0) / sample.length * snapshot.total : 0;
  // Skip a likely oversized full request instead of waiting for its rejection.
  // This is only a fast-path estimate; it never limits the export's size.
  if (estimatedBytes <= 4_000_000) {
    try {
      const res = await request({ ...pinned, format: "tsv" }, options.signal);
      const text = await res.text();
      if (options.signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
      if (isCompleteTsv(text, snapshot)) {
        options.onProgress?.({ completed: snapshot.total, total: snapshot.total });
        if (options.signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
        downloadTsv(text, downloadFilename(search.query));
        return;
      }
      // Also recover if a service returned only one page as a successful TSV.
    } catch (error) {
      if (!(error instanceof QueryError) || ![413, 503, 504].includes(error.status)) throw error;
    }
  }
  const blob = await collectAllPages(pinned, snapshot, queryAssemblies, options);
  if (options.signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
  downloadBlob(blob, downloadFilename(search.query));
}
export function downloadPage(result: QueryResult, query: string): void {
  const lines = ["#" + result.columns.join("\t"), ...result.rows.map(row => row.join("\t"))];
  downloadTsv(lines.join("\n") + "\n", downloadFilename(query).replace(/\.tsv$/, `-page-${result.page}.tsv`));
}

