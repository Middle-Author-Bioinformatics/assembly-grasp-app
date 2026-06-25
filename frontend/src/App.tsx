import { Header } from "./components/Header";
import { SearchView } from "./components/SearchView";
import { HAS_BACKEND, AG_CONFIG } from "./lib/assembly-config";

export default function App() {
  return (
    <div className="min-h-full relative z-10">
      <Header onTitleClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        {!HAS_BACKEND && (
          <div className="panel mb-6 px-4 py-3 text-sm border-warn/40">
            <div className="font-medium text-warn">Preview mode — no backend configured</div>
            <div className="text-fg-muted text-xs mt-1 leading-relaxed">
              This build was deployed without the Assembly Grasp env vars set. The
              UI loads but searches will fail. In Amplify → App settings →
              Environment variables, set:
              <ul className="mt-1.5 ml-4 list-disc space-y-0.5">
                <li><code className="font-mono">VITE_AG_API_BASE</code> — assembly-grasp Lambda Function URL</li>
                <li><code className="font-mono">VITE_AG_BUCKET</code> — S3 bucket name (display only)</li>
              </ul>
              Then redeploy. See <code className="font-mono">AWS_SETUP.md</code> in the repo.
            </div>
          </div>
        )}

        <SearchView />
      </main>
      <footer className="max-w-7xl mx-auto px-6 py-8 text-[11px] text-fg-faint font-mono">
        Reads from s3://{AG_CONFIG.bucket}/ncbi_assembly_info.tsv · © 2026 Middle
        Author Bioinformatics · For research and educational use only.
      </footer>
    </div>
  );
}
