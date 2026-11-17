import type { QueryResult, SearchRequest } from "./assembly-config";

export interface ExportProgress { completed: number; total: number }
export interface ExportOptions {
  signal?: AbortSignal;
  onProgress?: (progress: ExportProgress) => void;
}
export type ExportSnapshot = Pick<QueryResult, "columns" | "total" | "dataset_id"> & Partial<Pick<QueryResult, "rows">>;

function checkCancelled(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("Download cancelled", "AbortError");
}

export function isCompleteTsv(text: string, snapshot: ExportSnapshot): boolean {
  let header = false, rows = 0;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/\r$/, "").replace(/^\uFEFF/, "");
    if (!line) continue;
    if (line.startsWith("#")) {
      if (line.slice(1) === snapshot.columns.join("\t")) header = true;
    } else {
      if (!header || line.split("\t").length !== snapshot.columns.length) return false;
      rows++;
    }
  }
  return header && rows === snapshot.total;
}

/** Fetch every matching row, irrespective of the viewer's page or page size.
 * No partial file is offered if a request fails or the dataset changes.
 */
export async function collectAllPages(
  search: SearchRequest,
  snapshot: ExportSnapshot,
  loadPage: (search: SearchRequest, signal?: AbortSignal) => Promise<QueryResult>,
  options: ExportOptions = {},
): Promise<Blob> {
  const { signal, onProgress } = options;
  if (!snapshot.dataset_id || !Number.isSafeInteger(snapshot.total) || snapshot.total < 0 || !snapshot.columns.length) {
    throw new Error("Run a new search before downloading all matches.");
  }
  checkCancelled(signal);
  const parts: BlobPart[] = ["#" + snapshot.columns.join("\t") + "\n"];
  let completed = 0, sizeIndex = 0;
  // Each smaller size divides its predecessor, so a retry starts at the exact
  // next row even if an unusually wide page exceeds the API response limit.
  const sizes = [100, 50, 25, 5, 1];
  onProgress?.({ completed, total: snapshot.total });
  while (completed < snapshot.total) {
    checkCancelled(signal);
    const pageSize = sizes[sizeIndex], page = completed / pageSize + 1;
    let result: QueryResult;
    try {
      result = await loadPage({ ...search, page, page_size: pageSize, dataset_id: snapshot.dataset_id }, signal);
    } catch (error) {
      checkCancelled(signal);
      if ((error as { status?: number })?.status === 413 && sizeIndex < sizes.length - 1) {
        sizeIndex++;
        continue;
      }
      throw error;
    }
    checkCancelled(signal);
    const sameColumns = result.columns.length === snapshot.columns.length && result.columns.every((c, i) => c === snapshot.columns[i]);
    const expectedRows = Math.min(pageSize, snapshot.total - completed);
    if (result.dataset_id !== snapshot.dataset_id || result.total !== snapshot.total || !sameColumns) {
      throw new Error("The results changed during download. Run a new search and try again.");
    }
    if (result.page !== page || result.page_size !== pageSize || result.rows.length !== expectedRows ||
      result.rows.some(row => row.length !== snapshot.columns.length || row.some(cell => typeof cell !== "string"))) {
      throw new Error("An incomplete results page was received. Please retry the download.");
    }
    // Keep each completed page as a Blob instead of one huge combined string.
    // Original duplicate records are preserved; no accession deduplication.
    parts.push(new Blob([result.rows.map(row => row.join("\t")).join("\n") + "\n"]));
    completed += result.rows.length;
    onProgress?.({ completed, total: snapshot.total });
  }
  checkCancelled(signal);
  return new Blob(parts, { type: "text/tab-separated-values;charset=utf-8" });
}
