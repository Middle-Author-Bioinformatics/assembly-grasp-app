import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ShoppingBasket, X } from "lucide-react";
import { MAX_REFERENCES, newSelection, sameBioSample } from "../lib/genome-selection";
import { handoffSelection } from "../lib/tree-handoff";

export function GenomeTray({ genomes, onRemove, onClear }: { genomes: Record<string, string>[]; onRemove: (id: string) => void; onClear: () => void }) {
  const [message, setMessage] = useState("");
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const cleanup = useRef<() => void>();
  const id = useId();
  useEffect(() => () => cleanup.current?.(), []);
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [open]);
  useEffect(() => {
    if (open && !dialog.current?.contains(document.activeElement)) closeButton.current?.focus();
  }, [genomes, open]);
  const target = (import.meta.env.VITE_GTOTREE_APP_URL as string | undefined) || "";
  const repeats = sameBioSample(genomes);
  function show() { dialog.current?.showModal(); setOpen(true); }
  function closed() { setOpen(false); trigger.current?.focus(); }
  function close() { dialog.current?.close(); closed(); }
  function download() {
    const url = URL.createObjectURL(new Blob([JSON.stringify(newSelection(genomes), null, 2)], { type: "application/json" }));
    const a = document.createElement("a"); a.href = url; a.download = "taxonslueth-selection.json"; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function transfer() {
    try { cleanup.current?.(); cleanup.current = handoffSelection(target, newSelection(genomes), setMessage); }
    catch (error) { setMessage(error instanceof Error ? error.message : String(error)); }
  }
  return createPortal(<>
    <button ref={trigger} type="button" className="btn-primary genome-cart-launcher" onClick={show}
      aria-label={`Selected genomes (${genomes.length})`} aria-haspopup="dialog" aria-expanded={open} aria-controls={id}>
      <ShoppingBasket size={19} aria-hidden="true" />
      <span>Selected genomes</span><span className="genome-cart-count" aria-live="polite" aria-atomic="true">{genomes.length}</span>
    </button>
    <dialog ref={dialog} id={id} className="genome-cart-dialog" aria-labelledby={`${id}-title`} aria-describedby={`${id}-count`}
      onCancel={event => { event.preventDefault(); close(); }}
      onClose={() => { if (!dialog.current?.open) closed(); }}
      onClick={event => { if (event.target === event.currentTarget) close(); }}>
      <div className="genome-cart-content">
        <header className="genome-cart-heading">
          <div><h2 id={`${id}-title`}>Selected genomes</h2><p id={`${id}-count`}>{genomes.length} / {MAX_REFERENCES} reference genomes selected</p></div>
          <button ref={closeButton} type="button" className="icon-btn" aria-label="Close selected genomes" onClick={close} autoFocus><X size={19} /></button>
        </header>
        <div className="genome-cart-body">
          {repeats > 0 && <p className="text-sm text-warn mb-4" role="status">{repeats} selection(s) share a BioSample.</p>}
          {genomes.length === 0 ? <p className="genome-cart-empty">No genomes selected.</p> : <ul className="genome-cart-list">
            {genomes.map(row => <li key={row.assembly_accession}>
              <div className="genome-cart-record"><b className="font-mono">{row.assembly_accession}</b><span className="italic">{row.organism_name || "Organism unavailable"}</span>{row.infraspecific_name && <span>{row.infraspecific_name}</span>}</div>
              <button type="button" className="btn-ghost" aria-label={`Remove ${row.assembly_accession}`} onClick={() => onRemove(row.assembly_accession)}>Remove</button>
            </li>)}
          </ul>}
        </div>
        <footer className="genome-cart-actions">
          <div className="genome-cart-action-row"><button type="button" className="btn-ghost" disabled={!genomes.length} onClick={() => { onClear(); closeButton.current?.focus(); }}>Clear selection</button><button type="button" className="btn-ghost" onClick={close}>Continue browsing</button></div>
          <div className="genome-cart-action-row"><button type="button" className="btn-ghost" disabled={!genomes.length} onClick={download}>Download selection JSON</button><button type="button" className="btn-primary" disabled={!genomes.length || !target} onClick={transfer}>Build tree</button></div>
          {!target && <p className="text-xs text-fg-muted">GToTree transfer unavailable</p>}
          {message && <p className="text-sm" role="status">{message}</p>}
        </footer>
      </div>
    </dialog>
  </>, document.body);
}
