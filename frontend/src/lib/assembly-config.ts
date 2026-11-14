const env = import.meta.env;
export const AG_CONFIG = {
  apiBase: (env.VITE_AG_API_BASE as string | undefined)?.replace(/\/+$/, "") || "",
  bucket: (env.VITE_AG_BUCKET as string | undefined) || "assembly-grasp-bucket",
};
export const HAS_BACKEND = AG_CONFIG.apiBase !== "";
export type FilterOp = "eq" | "ne" | "contains" | "gte" | "lte" | "missing" | "present";
export interface AssemblyFilter { column: string; op: FilterOp; value: string }
export interface AssemblySchema {
  columns: string[]; facets: Record<string, string[]>; dataset_id: string; indexed: boolean;
}
export interface QueryResult {
  columns: string[]; rows: string[][]; total: number; page: number;
  page_size: number; pages: number; dataset_id: string; indexed: boolean; scanned: number;
}
export interface SearchRequest {
  query: string; filters: AssemblyFilter[]; page: number; page_size: number; dataset_id?: string;
}
export class QueryError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
async function request(payload: object): Promise<Response> {
  if (!HAS_BACKEND) throw new QueryError("No search service configured.", 0);
  let res: Response;
  try {
    res = await fetch(AG_CONFIG.apiBase, { method: "POST",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
  } catch { throw new QueryError("Could not reach the search service. Please try again.", 0); }
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
export async function queryAssemblies(search: SearchRequest): Promise<QueryResult> {
  const res = await request({ ...search, format: "json" });
  if (!res.headers.get("Content-Type")?.includes("application/json")) {
    throw new QueryError("Update the search service to enable filters and pages.", 0);
  }
  const result = await res.json();
  if (!Array.isArray(result.rows) || !Array.isArray(result.columns) || !Number.isInteger(result.total)) {
    throw new QueryError("Unexpected search response. Please try again.", 0);
  }
  return result;
}
export function downloadFilename(raw: string): string {
  return `${raw.trim().replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") || "assemblies"}.tsv`;
}
export function downloadTsv(tsv: string, filename: string): void {
  const blob = new Blob([tsv], { type: "text/tab-separated-values;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function downloadAll(search: SearchRequest): Promise<void> {
  const res = await request({ ...search, format: "tsv" });
  downloadTsv(await res.text(), downloadFilename(search.query));
}
export function downloadPage(result: QueryResult, query: string): void {
  const lines = ["#" + result.columns.join("\t"), ...result.rows.map(row => row.join("\t"))];
  downloadTsv(lines.join("\n") + "\n", downloadFilename(query).replace(/\.tsv$/, `-page-${result.page}.tsv`));
}
