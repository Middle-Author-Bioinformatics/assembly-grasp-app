import { useEffect, useRef, useState } from "react";
import { MAX_REFERENCES, newSelection, sameBioSample } from "../lib/genome-selection";
import { handoffSelection } from "../lib/tree-handoff";

export function GenomeTray({genomes,onRemove,onClear}: {genomes:Record<string,string>[];onRemove:(id:string)=>void;onClear:()=>void}) {
  const [message,setMessage]=useState("");
  const cleanup=useRef<()=>void>();
  useEffect(()=>()=>cleanup.current?.(),[]);
  const target=(import.meta.env.VITE_GTOTREE_APP_URL as string|undefined)||"";
  const repeats=sameBioSample(genomes);
  function download(){const url=URL.createObjectURL(new Blob([JSON.stringify(newSelection(genomes),null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="assembly-grasp-selection.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function transfer(){try{cleanup.current?.();cleanup.current=handoffSelection(target,newSelection(genomes),setMessage);}catch(e){setMessage(e instanceof Error?e.message:String(e));}}
  return <section className="panel genome-tray" aria-label="Selected reference genomes">
    <div className="panel-hd flex flex-wrap items-center gap-3"><div className="mr-auto"><h2 className="text-base font-semibold">{genomes.length} / {MAX_REFERENCES} reference genomes selected</h2><p className="text-xs text-fg-muted">Selections stay here across searches and pages. Add your own genomes in GToTree.</p></div>
      <button className="btn-ghost" disabled={!genomes.length} onClick={download}>Download selection JSON</button>
      <button className="btn-primary" disabled={!genomes.length||!target} onClick={transfer}>Build a phylogenomic tree →</button>
    </div>
    <div className="panel-bd space-y-3">
      {!target&&<p className="text-xs text-fg-muted">Automatic transfer is not connected yet. Download your selection JSON and import it in GToTree.</p>}
      {repeats>0&&<p className="text-xs text-warn" role="status">{repeats} additional selection(s) share a BioSample with another selected assembly. Review possible GenBank/RefSeq counterparts before running.</p>}
      <details><summary className="text-sm cursor-pointer">Review selected genomes</summary><ul className="selection-list">{genomes.map(row=><li key={row.assembly_accession}><span><b>{row.assembly_accession}</b> · {row.organism_name||"Organism unavailable"} {row.infraspecific_name||""}</span><button className="btn-ghost" aria-label={`Remove ${row.assembly_accession}`} onClick={()=>onRemove(row.assembly_accession)}>Remove</button></li>)}</ul><button className="btn-ghost mt-3" disabled={!genomes.length} onClick={onClear}>Clear selection</button></details>
      {message&&<p className="text-sm" role="status">{message}</p>}
    </div>
  </section>;
}
