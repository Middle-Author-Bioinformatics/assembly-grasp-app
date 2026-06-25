import { colIndex, type ParsedTsv } from "../lib/assembly-config";

// A handful of the most useful columns to surface in the preview. The FULL set
// of columns is preserved in the downloaded TSV — this is just a readable
// at-a-glance table.
const PREVIEW_COLS = [
  "assembly_accession",
  "organism_name",
  "infraspecific_name",
  "assembly_level",
  "refseq_category",
  "seq_rel_date",
  "asm_name",
];

const MAX_PREVIEW_ROWS = 50;

export function ResultsTable({ parsed }: { parsed: ParsedTsv }) {
  const { header, rows } = parsed;

  // Resolve the preview column indices that actually exist in this header.
  const cols = PREVIEW_COLS
    .map((name) => ({ name, idx: colIndex(header, name) }))
    .filter((c) => c.idx >= 0);

  const shown = rows.slice(0, MAX_PREVIEW_ROWS);

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="bg-surface-2 text-fg-muted">
            {cols.map((c) => (
              <th
                key={c.name}
                className="text-left font-medium px-3 py-2 whitespace-nowrap border-b border-border">
                {c.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i} className="odd:bg-surface even:bg-surface-2/40">
              {cols.map((c) => {
                const val = row[c.idx] ?? "";
                const display = val === "na" || val === "" ? "—" : val;
                return (
                  <td
                    key={c.name}
                    className={`px-3 py-1.5 align-top border-b border-border-soft ${
                      c.name === "assembly_accession" || c.name === "asm_name"
                        ? "font-mono"
                        : ""
                    } ${c.name === "organism_name" ? "italic" : ""}`}>
                    {display}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > MAX_PREVIEW_ROWS && (
        <div className="px-3 py-2 text-[11px] text-fg-subtle bg-surface-2 border-t border-border">
          Showing first {MAX_PREVIEW_ROWS} of {rows.length} rows — the download
          contains all of them, with every column.
        </div>
      )}
    </div>
  );
}
