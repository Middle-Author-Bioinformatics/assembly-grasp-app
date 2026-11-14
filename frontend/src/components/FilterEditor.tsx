import type { AssemblyFilter, AssemblySchema, FilterOp } from "../lib/assembly-config";
const OPERATORS: [FilterOp, string][] = [["eq", "equals"], ["ne", "does not equal"],
  ["contains", "contains"], ["gte", "at least (number)"], ["lte", "at most (number)"],
  ["missing", "is missing"], ["present", "is present"]];
export function FilterEditor({ schema, filters, onChange, disabled }: {
  schema: AssemblySchema; filters: AssemblyFilter[];
  onChange: (filters: AssemblyFilter[]) => void; disabled: boolean;
}) {
  function patch(index: number, change: Partial<AssemblyFilter>) {
    onChange(filters.map((rule, i) => i === index ? { ...rule, ...change } : rule));
  }
  return <div className="space-y-3">
    <div className="flex flex-wrap items-center gap-3">
      <span className="text-xs text-fg-muted">Filters · all conditions must match</span>
      <button type="button" className="btn-ghost" disabled={disabled || filters.length >= 20}
        onClick={() => onChange([...filters, { column: schema.columns.includes("assembly_level") ? "assembly_level" : schema.columns[0], op: "eq", value: "" }])}>+ Add filter</button>
      {filters.length > 0 && <button type="button" className="btn-ghost" disabled={disabled} onClick={() => onChange([])}>Clear filters</button>}
    </div>
    {filters.map((rule, i) => <div key={i} className="flex flex-col sm:flex-row gap-2">
      <select aria-label={`Filter ${i + 1} column`} className="field min-w-0 flex-1" disabled={disabled} value={rule.column}
        onChange={e => patch(i, { column: e.target.value, value: "" })}>
        {schema.columns.map(c => <option key={c} value={c}>{c}</option>)}
      </select>
      <select aria-label={`Filter ${i + 1} operator`} className="field sm:w-48" disabled={disabled} value={rule.op}
        onChange={e => patch(i, { op: e.target.value as FilterOp })}>
        {OPERATORS.map(([op, label]) => <option key={op} value={op}>{label}</option>)}
      </select>
      {rule.op !== "missing" && rule.op !== "present" && <>
        <input aria-label={`Filter ${i + 1} value`} className="field min-w-0 flex-1" disabled={disabled}
          list={`filter-options-${i}`} value={rule.value} placeholder={rule.op === "gte" || rule.op === "lte" ? "Numeric value" : "Value (case-insensitive)"}
          onChange={e => patch(i, { value: e.target.value })} />
        <datalist id={`filter-options-${i}`}>{(schema.facets[rule.column] || []).map(v => <option key={v} value={v} />)}</datalist>
      </>}
      <button type="button" className="btn-ghost" aria-label={`Remove filter ${i + 1}`} disabled={disabled}
        onClick={() => onChange(filters.filter((_, idx) => idx !== i))}>Remove</button>
    </div>)}
    {filters.length > 0 && <p className="text-[11px] text-fg-subtle">Numeric values use the source table’s units. Missing values are excluded from numeric comparisons. Press Search &amp; view to apply changes.</p>}
  </div>;
}
