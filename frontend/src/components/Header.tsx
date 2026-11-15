import { useTheme } from "../lib/theme";
import { HAS_BACKEND } from "../lib/assembly-config";
import { Sun, Moon, AlertTriangle } from "lucide-react";
import mabLogo from "/mab-logo.png";

interface Props {
  onTitleClick?: () => void;
}

export function Header({ onTitleClick }: Props) {
  const { theme, toggle } = useTheme();

  return (
    <header className="sticky top-0 z-30 bg-bg/85 backdrop-blur border-b border-border">
      <div className="assembly-header">
        <button type="button" onClick={onTitleClick}
          className="assembly-brand" aria-label="Middle Author Bioinformatics — back to top">
          <img src={mabLogo} alt="Middle Author Bioinformatics"
            width={2048} height={486} className="assembly-brand-logo" />
        </button>

        <button type="button" onClick={onTitleClick}
          className="assembly-heading" aria-label="Assembly Grasp — back to top">
          <span className="assembly-app-name">Assembly Grasp</span>
        </button>

        <div className="assembly-header-actions">
          {!HAS_BACKEND && (
            <span className="pill border-warn/40 bg-warn/10 text-warn">
              <AlertTriangle size={12} /> preview mode
            </span>
          )}
          <button type="button" onClick={toggle} className="icon-btn"
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
            {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
          </button>
        </div>
      </div>
    </header>
  );
}

