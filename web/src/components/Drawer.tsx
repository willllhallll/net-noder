import type { ReactNode } from "react";

interface Props {
  // Climbs back up one overlay layer. The button sits in a fixed position so
  // the control to "go back" / "exit" is always in the same place.
  onBack: () => void;
  // "✕" closes a top-level overlay (focus); "←" steps back one drill-down level.
  backIcon?: "✕" | "←";
  children: ReactNode;
}

// Shared chrome for the right-side drawer: the panel + the single top button.
export default function Drawer({ onBack, backIcon = "✕", children }: Props) {
  return (
    <aside className="conv-drawer" onClick={(e) => e.stopPropagation()}>
      <button className="modal-close" onClick={onBack}>
        {backIcon}
      </button>
      {children}
    </aside>
  );
}
