import {ACCOUNT_ENABLED} from "./lib/account-config";
import {AccountProvider,AccountGate} from "./components/Account";
import { Header } from "./components/Header";
import { SearchView } from "./components/SearchView";
import { HAS_BACKEND } from "./lib/assembly-config";

function Workspace() {
  return (
    <div className="min-h-full relative z-10">
      <Header onTitleClick={() => window.scrollTo({ top: 0, behavior: "smooth" })} />
      <main className="max-w-7xl mx-auto px-6 py-8">
        {!HAS_BACKEND && (
          <div className="panel mb-6 px-4 py-3 text-sm border-warn/40">
            <div className="font-medium text-warn" role="alert">Search service unavailable</div>
          </div>
        )}

        <SearchView />
      </main>
      <footer className="max-w-7xl mx-auto px-6 py-8 text-[11px] text-fg-faint font-mono">
        © 2026 Middle Author Bioinformatics · For research and educational use only.
      </footer>
    </div>
  );
}


export default function App(){return ACCOUNT_ENABLED?<AccountProvider><AccountGate header={<Header/>}><Workspace/></AccountGate></AccountProvider>:<Workspace/>;}
