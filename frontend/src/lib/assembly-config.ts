// Assembly Grasp — runtime config + Lambda client.
//
// Architecture (backend-less):
//
//   React SPA (Amplify) ──POST {query}──► assembly-grasp Lambda (Function URL)
//                       ◄──filtered TSV──┘            │
//                                                     │ streams & filters
//                                                     ▼
//                              s3://assembly-grasp-bucket/ncbi_assembly_info.tsv
//
// The Lambda reads the NCBI assembly_summary-style TSV, keeps the rows whose
// `organism_name` starts with the user's genus (or genus + species), and returns
// them as a TSV. The browser turns that into a Blob and downloads it immediately.
//
// All env vars are read at BUILD TIME from Amplify's "Environment variables".

const env = import.meta.env;

export const AG_CONFIG = {
  /** assembly-grasp Lambda Function URL.
   *    POST { query }  → text/tab-separated-values (the filtered rows) */
  apiBase: (env.VITE_AG_API_BASE as string | undefined)?.replace(/\/+$/, "") || "",
  /** S3 bucket the Lambda reads from (informational; the Lambda knows it). */
  bucket: (env.VITE_AG_BUCKET as string | undefined) || "assembly-grasp-bucket",
};

export const HAS_BACKEND = AG_CONFIG.apiBase !== "";

// ---------------------------------------------------------------------------
// Query model.
//
// The user types either a single genus ("Drosophila") or a binomial
// ("Drosophila melanogaster"). We do light client-side validation: letters,
// spaces, hyphens, periods; 1-3 words is the sweet spot (genus, species,
// optional subspecies). The Lambda is the source of truth for matching.
// ---------------------------------------------------------------------------
export type QueryKind = "empty" | "genus" | "species" | "invalid";

const NAME_RX = /^[A-Za-z][A-Za-z .'-]*$/;

export function classifyQuery(raw: string): QueryKind {
  const q = raw.trim().replace(/\s+/g, " ");
  if (!q) return "empty";
  const words = q.split(" ");
  if (words.some((w) => !NAME_RX.test(w))) return "invalid";
  if (words.length === 1) return "genus";
  return "species"; // two or more words
}

/** A safe download filename mirroring what the Lambda sets. */
export function downloadFilename(raw: string): string {
  const base =
    raw.trim().replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^_+|_+$/g, "") ||
    "assemblies";
  return `${base}.tsv`;
}

// ---------------------------------------------------------------------------
// TSV parsing (for the in-app preview only — the raw TSV is what gets saved).
// ---------------------------------------------------------------------------
export interface ParsedTsv {
  /** The "##" description line(s), if any. */
  comments: string[];
  /** Column headers (from the "#assembly_accession\t..." line). */
  header: string[];
  /** Data rows, each a list of cell strings aligned to `header`. */
  rows: string[][];
}

export function parseTsv(text: string): ParsedTsv {
  const comments: string[] = [];
  let header: string[] = [];
  const rows: string[][] = [];

  for (const line of text.split("\n")) {
    if (!line) continue;
    if (line.startsWith("##")) {
      comments.push(line.replace(/^#+\s*/, ""));
      continue;
    }
    if (line.startsWith("#")) {
      header = line.replace(/^#/, "").split("\t");
      continue;
    }
    rows.push(line.split("\t"));
  }
  return { comments, header, rows };
}

/** Index of a named column in the header (case-insensitive), or -1. */
export function colIndex(header: string[], name: string): number {
  const lower = name.toLowerCase();
  return header.findIndex((h) => h.toLowerCase() === lower);
}

// ---------------------------------------------------------------------------
// The one network call: ask the Lambda for matching rows.
// ---------------------------------------------------------------------------
export interface QueryResult {
  /** The raw TSV body exactly as returned (header + matching rows). */
  tsv: string;
  /** Parsed view for the preview table. */
  parsed: ParsedTsv;
  /** Number of data rows matched (from the X-Assembly-Match-Count header, with
   *  a fallback to the parsed row count). */
  matchCount: number;
  /** Total data rows the Lambda scanned, if reported. */
  totalRows: number | null;
  /** The download filename (from Content-Disposition, else derived). */
  filename: string;
}

export class QueryError extends Error {
  status: number;
  detail?: string;
  constructor(message: string, status: number, detail?: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

function parseFilename(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const m = /filename="?([^"]+)"?/.exec(disposition);
  return m ? m[1] : fallback;
}

export async function queryAssemblies(rawQuery: string): Promise<QueryResult> {
  const query = rawQuery.trim().replace(/\s+/g, " ");
  if (!AG_CONFIG.apiBase) {
    throw new QueryError(
      "No backend configured. Set VITE_AG_API_BASE in Amplify → Environment variables.",
      0,
    );
  }

  let res: Response;
  try {
    res = await fetch(AG_CONFIG.apiBase, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
  } catch (e: any) {
    throw new QueryError(
      `Network error calling the Lambda (check the Function URL and its CORS): ${e?.message ?? e}`,
      0,
    );
  }

  const ctype = res.headers.get("Content-Type") || "";

  if (!res.ok) {
    // Error responses are JSON: { error, detail? }
    let msg = `Request failed (HTTP ${res.status})`;
    let detail: string | undefined;
    try {
      const body = await res.json();
      msg = body?.error || msg;
      detail = body?.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new QueryError(msg, res.status, detail);
  }

  const tsv = await res.text();
  const parsed = parseTsv(tsv);

  const headerCount = res.headers.get("X-Assembly-Match-Count");
  const totalHeader = res.headers.get("X-Assembly-Total-Rows");
  const matchCount = headerCount ? parseInt(headerCount, 10) : parsed.rows.length;
  const totalRows = totalHeader ? parseInt(totalHeader, 10) : null;

  return {
    tsv,
    parsed,
    matchCount: Number.isFinite(matchCount) ? matchCount : parsed.rows.length,
    totalRows: totalRows !== null && Number.isFinite(totalRows) ? totalRows : null,
    filename: parseFilename(
      res.headers.get("Content-Disposition"),
      downloadFilename(query),
    ),
  };
}

/** Trigger a browser download of `tsv` as `filename`. */
export function downloadTsv(tsv: string, filename: string): void {
  const blob = new Blob([tsv], { type: "text/tab-separated-values;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick so the click has a chance to start the download.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
