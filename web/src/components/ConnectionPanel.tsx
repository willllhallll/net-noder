import { useMemo } from "react";
import type { ConnectionStacks, LabelOpts, Tier } from "../types";
import type { LayerLookup } from "../format";
import {
  edgeStyle,
  fmtBytes,
  fmtNum,
  mostSpecific,
  nodeColor,
  resolveLabel,
  TIER_ORDER,
} from "../format";
import Drawer from "./Drawer";
import PortColumn, { PortChip } from "./PortColumn";

interface Props {
  conn: ConnectionStacks;
  labelOpts: LabelOpts;
  activeTier: Tier;
  lk: LayerLookup;
  onBack: () => void;
}

// Collapse one side's per-stack port lists into a single colour-coded chip list. Each
// port takes the colour of the stack it belongs to AT THE ACTIVE TIER -- the same
// edgeStyle the flow arrows use -- so the column recolours in lock-step with the graph
// when the tier filter steps. A port seen under several stacks keeps the most-specific
// (highest-tier) one's colour, so the chip matches the busiest/deepest arrow it sits on.
function colourPorts(
  stacks: ConnectionStacks["stacks"],
  side: "ports_a" | "ports_b",
  activeTier: Tier,
  lk: LayerLookup
): PortChip[] {
  const best = new Map<number, { colour: string; tier: number }>();
  for (const st of stacks) {
    const colour = edgeStyle(st.layers, activeTier, lk).color;
    const top = mostSpecific(st.layers, lk);
    const tier = top ? TIER_ORDER.indexOf(lk.map.get(top)?.tier ?? "link") : -1;
    for (const port of st[side]) {
      const prev = best.get(port);
      if (!prev || tier > prev.tier) best.set(port, { colour, tier });
    }
  }
  return Array.from(best.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([port, v]) => ({ port, colour: v.colour }));
}

// The whole-connection overview (edge clicked, no stack chosen yet): neutral A<->B
// volumes plus every port in use on each side, colour-coded to the stack it belongs to.
// The protocol stacks themselves are explored by clicking the grouped flow arrows.
export default function ConnectionPanel({ conn, labelOpts, activeTier, lk, onBack }: Props) {
  const A = resolveLabel(labelOpts, conn.name_a, conn.whois_name_a, conn.ip_a);
  const B = resolveLabel(labelOpts, conn.name_b, conn.whois_name_b, conn.ip_b);

  const portsA = useMemo(
    () => colourPorts(conn.stacks, "ports_a", activeTier, lk),
    [conn.stacks, activeTier, lk]
  );
  const portsB = useMemo(
    () => colourPorts(conn.stacks, "ports_b", activeTier, lk),
    [conn.stacks, activeTier, lk]
  );

  return (
    <Drawer onBack={onBack} backIcon="✕">
      <h2>
        <span className="dot" style={{ background: nodeColor({ colour: conn.colour_a }) }} />
        {A}
        <span className="conv-arrow">↔</span>
        {B}
        <span className="dot" style={{ background: nodeColor({ colour: conn.colour_b }) }} />
      </h2>

      <div className="badges">
        <span className="badge">{fmtNum(conn.flow_count)} flows</span>
        <span className="badge">{fmtNum(conn.stacks.length)} stacks</span>
      </div>

      <table className="kv">
        <tbody>
          <tr>
            <td>{A} → {B}</td>
            <td>{fmtNum(conn.pkts_a2b)} pkts · {fmtBytes(conn.bytes_a2b)}</td>
          </tr>
          <tr>
            <td>{B} → {A}</td>
            <td>{fmtNum(conn.pkts_b2a)} pkts · {fmtBytes(conn.bytes_b2a)}</td>
          </tr>
        </tbody>
      </table>

      <h3>Ports in use</h3>
      <div className="proto-hint muted">
        Every port observed on each side, coloured by the stack it carries. Click a flow
        arrow for that stack&apos;s full breadcrumb.
      </div>
      <div className="port-cols">
        <PortColumn label={A} ports={portsA} />
        <PortColumn label={B} ports={portsB} />
      </div>
    </Drawer>
  );
}
