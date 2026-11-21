import { validateSelection, type GenomeSelection } from "./genome-selection";

export function treeTarget(configured: string): URL {
  const target = new URL(configured);
  if (target.protocol !== "https:" || target.username || target.password) throw new Error("The GToTree address must be an HTTPS URL.");
  return target;
}

/** User-initiated, origin-checked handoff. Genome metadata never enters a URL. */
export function handoffSelection(configured: string, selection: GenomeSelection, onStatus: (message: string) => void): () => void {
  const target = treeTarget(configured), payload = validateSelection(selection);
  const nonce = crypto.randomUUID();
  target.hash = new URLSearchParams({"mab-import":nonce,source:window.location.origin}).toString();
  let child: Window | null = null;
  let timer: ReturnType<typeof setTimeout>;
  const cleanup = () => { window.removeEventListener("message", receive); clearTimeout(timer); };
  const receive = (event: MessageEvent) => {
    if (!child || event.source !== child || event.origin !== target.origin || event.data?.nonce !== nonce) return;
    if (event.data?.type === "mab:gtotree-ready") child.postMessage({type:"mab:genome-selection",nonce,selection:payload},target.origin);
    if (event.data?.type === "mab:gtotree-imported") { cleanup(); onStatus("Selection transferred. Continue in the GToTree tab and add your own genomes if needed."); }
  };
  window.addEventListener("message",receive);
  child = window.open(target.href,"_blank");
  if (!child) { cleanup(); throw new Error("The new tab was blocked. Allow popups, or download the selection JSON and import it in GToTree."); }
  timer = setTimeout(() => {cleanup();onStatus("If the selection did not appear, download its JSON file and use Import selection in GToTree. The two app origins must be configured for automatic transfer.");},60000);
  onStatus("Opening GToTree and transferring the selected reference genomes…");
  return cleanup;
}
