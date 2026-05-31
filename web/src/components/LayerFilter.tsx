import type { Tier } from "../types";
import { TIER_ORDER } from "../format";

export interface TierLegendItem {
  token: string;
  colour: string;
  unresolved?: boolean;
}

interface Props {
  activeTier: Tier;
  setActiveTier: (t: Tier) => void;
  // Tokens currently visible at the active tier, with their persisted colours.
  tierLegend: TierLegendItem[];
  nodeCount: number;
  edgeCount: number;
}

// The headline control: a tier stepper (link -> network -> transport ->
// application) that recolours + relabels the flow edges by the protocol they carry
// at the active tier (flows with none at the tier go neutral grey/dashed). A tier is
// ALWAYS selected — it defaults to transport. The legend below is SCOPED to the
// active tier so every visible colour is explained.
export default function LayerFilter({
  activeTier,
  setActiveTier,
  tierLegend,
  nodeCount,
  edgeCount,
}: Props) {
  return (
    <div className="layer-filter">
      <div className="legend-title">
        {nodeCount} endpoints · {edgeCount} flows
      </div>

      <div className="tier-stepper">
        {TIER_ORDER.map((t) => (
          <button
            key={t}
            className={`tier-btn ${activeTier === t ? "active" : ""}`}
            onClick={() => setActiveTier(t)}
          >
            {t}
          </button>
        ))}
      </div>

      {tierLegend.length > 0 ? (
        <div className="tier-legend">
          {tierLegend.map((it) => (
            <LegendRow
              key={it.token}
              color={it.colour}
              label={it.unresolved ? `${it.token} — unresolved` : it.token}
            />
          ))}
        </div>
      ) : (
        <div className="legend-hint">
          No {activeTier} protocols in the current view.
        </div>
      )}
    </div>
  );
}

function LegendRow({ color, label }: { color: string; label: string }) {
  return (
    <div className="legend-row">
      <span className="dot" style={{ background: color }} />
      {label}
    </div>
  );
}
