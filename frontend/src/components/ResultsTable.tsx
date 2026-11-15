import { useState } from "react";
import type { QueryResult } from "../lib/assembly-config";
const DEFAULT_COLUMNS = ["assembly_accession", "organism_name", "group", "infraspecific_name",
  "assembly_level", "refseq_category", "seq_rel_date", "asm_name",
  "bioproject", "biosample", "genome_size", "genome_size_ungapped", "gc_percent",
  "replicon_count", "scaffold_count", "contig_count", "annotation_provider",
  "annotation_name", "annotation_date", "total_gene_count",
  "protein_coding_gene_count", "non_coding_gene_count"];

const NCBI_FIELDS: Record<string, { pattern: RegExp; base: string }> = {
  assembly_accession: { pattern: /^GC[AF]_\d+(?:\.\d+)?$/i, base: "https://www.ncbi.nlm.nih.gov/datasets/genome/" },
  bioproject: { pattern: /^PRJ(?:NA|EB|DB)\d+$/i, base: "https://www.ncbi.nlm.nih.gov/bioproject/" },
  biosample: { pattern: /^SAM(?:N|EA|D)\d+$/i, base: "https://www.ncbi.nlm.nih.gov/biosample/" },
};

function cellValue(column: string, value: string | undefined) {
  if (!value?.trim() || /^(na|n\/a)$/i.test(value.trim())) return "—";
  const field = NCBI_FIELDS[column];
  if (!field) return value;
  // Preserve separators and text when a cell contains more than one accession.
  return value.split(/([,;\s]+)/).map((part, i) =>
    field.pattern.test(part) ? (
      <a key={i} href={field.base + encodeURIComponent(part.toUpperCase()) + "/"}
        target="_blank" rel="noopener noreferrer" title={`View ${part} on NCBI`}
        className="text-accent underline decoration-accent/50 underline-offset-2 hover:decoration-accent">
        {part}
      </a>
    ) : part
  );
}

export function ResultsTable({ result }: { result: QueryResult }) {
  const [selection, setSelection] = useState<string[] | null>(null);
  const columns = result.columns;
  const selected = selection ?? DEFAULT_COLUMNS.filter(c => columns.includes(c));
  const leading = ["assembly_accession", "organism_name", "group"];
  const displayColumns = [...leading.filter(c => columns.includes(c)), ...columns.filter(c => !leading.includes(c))];
  const shown = displayColumns.map(name => ({ name, idx: columns.indexOf(name) })).filter(c => selected.includes(c.name));
  return <div className="space-y-3">
    <details className="text-xs">
      <summary className="cursor-pointer text-fg-muted">Choose columns ({shown.length} of {columns.length})</summary>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn-ghost" onClick={() => setSelection([...columns])}>Show all columns</button>
        <button className="btn-ghost" onClick={() => setSelection(null)}>Reset columns</button>
      </div>
      <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {columns.map(name => <label key={name} className="flex items-center gap-2 break-all">
          <input type="checkbox" checked={selected.includes(name)} onChange={e => {
            setSelection(e.target.checked ? [...selected, name] : selected.filter(c => c !== name));
          }} /> {name}
        </label>)}
      </div>
    </details>
    <div className="overflow-x-auto rounded-lg border border-border" role="region" aria-label="Assembly results" tabIndex={0}>
      <table className="w-full text-xs border-collapse">
        <thead><tr className="bg-surface-2 text-fg-muted">{shown.map(c =>
          <th key={c.name} scope="col" className="text-left font-medium px-3 py-2 whitespace-nowrap border-b border-border">{c.name === "group" ? "Group" : c.name}</th>
        )}</tr></thead>
        <tbody>{result.rows.map((row, i) => <tr key={i} className="odd:bg-surface even:bg-surface-2/40">
          {shown.map(c => <td key={c.name} className={`px-3 py-1.5 align-top border-b border-border-soft ${c.name === "organism_name" ? "italic" : ""}`}>
            {cellValue(c.name, row[c.idx])}
          </td>)}
        </tr>)}</tbody>
      </table>
    </div>
    {shown.length === 0 && <p className="text-xs text-fg-muted">Select a column above to display the table.</p>}
  </div>;
}

