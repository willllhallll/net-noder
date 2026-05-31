import type { Layer, Tier } from "./types";

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 2 : 1)} ${u[i]}`;
}

export function fmtNum(n: number): string {
  return n.toLocaleString();
}

export function fmtTime(t: number | null): string {
  if (t == null) return "—";
  return new Date(t * 1000).toLocaleString();
}

// Node colour by kind ONLY (no local/remote judgement): multicast purple,
// broadcast red, unicast sky-blue. Edges are coloured by protocol, not by node.
export function nodeColor(kind: string): string {
  if (kind === "multicast") return "#a855f7";
  if (kind === "broadcast") return "#ef4444";
  return "#60a5fa";
}

// Neutral edge colour: used in the default (no-tier) view and for edges with no
// protocol at the active tier (which also dim out).
export const NEUTRAL_EDGE = "#64748b";

export const TIER_ORDER: Tier[] = ["link", "network", "transport", "application"];

export function nodeSize(bytes: number): number {
  return Math.max(10, Math.min(60, 8 + 7 * Math.log10(bytes + 10)));
}

export function edgeWidth(bytes: number): number {
  return Math.max(1, Math.min(12, 0.5 + 1.6 * Math.log10(bytes + 10)));
}

// Build a lookup from the /api/layers response: token -> Layer (tier + colour),
// plus a rank (its position, which the API returns count-descending) so the most
// common protocol can be chosen as an edge's representative within a tier.
export interface LayerLookup {
  map: Map<string, Layer>;
  rank: Map<string, number>;
}

export function buildLayerLookup(layers: Layer[]): LayerLookup {
  const map = new Map<string, Layer>();
  const rank = new Map<string, number>();
  layers.forEach((l, i) => {
    map.set(l.layer, l);
    rank.set(l.layer, i);
  });
  return { map, rank };
}

// True for tshark stop-markers ('data') — a payload present but unidentified, i.e.
// the point where dissection stopped. These are NOT real protocols.
export function isUnresolved(token: string, lk: LayerLookup): boolean {
  return lk.map.get(token)?.unresolved ?? token === "data";
}

function bestByRank(tokens: string[], lk: LayerLookup): string | null {
  let best: string | null = null;
  let bestRank = Infinity;
  for (const t of tokens) {
    const r = lk.rank.get(t) ?? Infinity;
    if (r < bestRank) {
      bestRank = r;
      best = t;
    }
  }
  return best;
}

// The most-specific (highest-tier) RESOLVED token on an edge — its default label.
// Stop-markers ('data') are skipped so this is always the deepest *recovered*
// protocol, not the point where identification gave up.
export function mostSpecific(layers: string[], lk: LayerLookup): string | null {
  let best: string | null = null;
  let bestTier = -1;
  let bestRank = Infinity;
  for (const t of layers) {
    if (isUnresolved(t, lk)) continue;
    const info = lk.map.get(t);
    const tierIdx = info ? TIER_ORDER.indexOf(info.tier) : -1;
    const r = lk.rank.get(t) ?? Infinity;
    if (tierIdx > bestTier || (tierIdx === bestTier && r < bestRank)) {
      bestTier = tierIdx;
      bestRank = r;
      best = t;
    }
  }
  return best;
}

// The token an edge carries at the active tier — preferring a real protocol, and
// only falling back to a stop-marker when that is all the tier has.
export function tokenAtTier(
  layers: string[],
  tier: Tier,
  lk: LayerLookup
): string | null {
  const here = layers.filter((t) => lk.map.get(t)?.tier === tier);
  const real = here.filter((t) => !isUnresolved(t, lk));
  return bestByRank(real.length ? real : here, lk);
}

// A flow edge's appearance at the active tier. `dashed` means "this flow carries no
// protocol at this tier" (drawn neutral grey but visible), NOT an unresolved payload.
export interface EdgeStyle {
  color: string;
  label: string;
  opacity: number;
  token: string | null; // the protocol the edge currently represents (its label)
  dashed: boolean;
}

export function edgeStyle(
  layers: string[],
  activeTier: Tier,
  lk: LayerLookup
): EdgeStyle {
  const tok = tokenAtTier(layers, activeTier, lk);
  if (!tok) {
    // No protocol at this tier (e.g. ICMP has no application layer): keep the edge
    // neutral but VISIBLE — grey dashed, never faded to near-invisible black.
    return { color: NEUTRAL_EDGE, label: "", opacity: 0.5, token: null, dashed: true };
  }
  // Every recovered token — including the 'data' stop-marker — is drawn solid in its
  // own (non-grey) palette colour: unresolved payload is still real, worthwhile data.
  return {
    color: lk.map.get(tok)?.colour ?? NEUTRAL_EDGE,
    label: tok,
    opacity: 0.9,
    token: tok,
    dashed: false,
  };
}
