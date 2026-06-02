import type { ReactNode } from "react";
import type { LabelOpts } from "../types";

interface Props {
  labelOpts: LabelOpts;
  setLabelOpts: (o: LabelOpts) => void;
  nodeCount: number;
  edgeCount: number;
  // What the edge count represents in the current view ("connections" | "flows").
  edgeLabel: string;
  // The per-view filter body: CategoryFilter (hosts/focus) or TierFilter (flows).
  children: ReactNode;
}

// The bottom-left control panel, shown in every view. It owns the count header and the
// Labels toggles (given name / WHOIS name) — kept permanently above whichever per-view
// filter is passed as children, so the label choice persists as you move between views.
export default function FilterPanel({
  labelOpts,
  setLabelOpts,
  nodeCount,
  edgeCount,
  edgeLabel,
  children,
}: Props) {
  return (
    <div className="filter-panel">
      <div className="legend-title">
        {nodeCount} endpoints · {edgeCount} {edgeLabel}
      </div>

      <div className="filter-section">
        <span>Labels</span>
        <div className="label-toggles">
          <label className="label-toggle">
            <input
              type="checkbox"
              checked={labelOpts.given}
              onChange={(e) => setLabelOpts({ ...labelOpts, given: e.target.checked })}
            />
            Given name
          </label>
          <label className="label-toggle">
            <input
              type="checkbox"
              checked={labelOpts.whois}
              onChange={(e) => setLabelOpts({ ...labelOpts, whois: e.target.checked })}
            />
            WHOIS name
          </label>
        </div>
      </div>

      {children}
    </div>
  );
}
