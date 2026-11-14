import { useState } from "react";
import type { QueryResult } from "../lib/assembly-config";
const DEFAULT_COLUMNS = ["assembly_accession", "organism_name", "infraspecific_name",
  "assembly_level", "refseq_category", "seq_rel_date", "asm_name"];
export function ResultsTable({ result }: { result: QueryResult }) {
  const [selection, setSelection] = useState<string[] | null>(null);
  const columns = result.columns;
  const selected = selection ?? DEFAULT_COLUMNS.filter(c => columns.includes(c));
  const shown = columns.map((name, idx) => ({ name, idx })).filter(c => selected.includes(c.name));
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
          <th key={c.name} scope="col" className="text-left font-medium px-3 py-2 whitespace-nowrap border-b border-border">{c.name}</th>
        )}</tr></thead>
        <tbody>{result.rows.map((row, i) => <tr key={i} className="odd:bg-surface even:bg-surface-2/40">
          {shown.map(c => <td key={c.name} className={`px-3 py-1.5 align-top border-b border-border-soft ${c.name === "organism_name" ? "italic" : ""}`}>
            {!row[c.idx] || row[c.idx] === "na" ? "—" : row[c.idx]}
          </td>)}
        </tr>)}</tbody>
      </table>
    </div>
    {shown.length === 0 && <p className="text-xs text-fg-muted">Select a column above to display the table.</p>}
  </div>;
}
