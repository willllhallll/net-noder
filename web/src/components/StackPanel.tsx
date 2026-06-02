import { useMemo } from "react";
import type { ConnectionStack, ConnectionStacks, LabelOpts, Tier } from "../types";
import type { LayerLookup } from "../format";
import {
  edgeStyle,
  fmtBytes,
  fmtNum,
  fmtTime,
  isEncrypted,
  isUnresolved,
  NEUTRAL_EDGE,
  nodeColor,
  resolveLabel,
  shortVersion,
  versionOwnerToken,
} from "../format";
import Drawer from "./Drawer";
import PortColumn, { PortChip } from "./PortColumn";

interface Props {
  conn: ConnectionStacks;
  stack: ConnectionStack;
  labelOpts: LabelOpts;
  activeTier: Tier;
  lk: LayerLookup;
  onBack: () => void;
}

// One stack's drawer: the de-noised dissected stack shared by every flow in the group as
// a breadcrumb (eth › ip › tcp › tls, deepest last — sealed payload-content like a TLS 1.2
// certificate cascade is cut upstream by position, so it never appears). The principal
// protocol's chip carries its decoded version badge (e.g. "tls · 1.3", "http · 1.1") and
// any encryption-boundary chip carries a 🔒 to mark where the payload is sealed. Below:
// every port each endpoint used on this stack as two colour-coded columns. Every chip is
// the stack's own colour (one stack = one colour at the active tier), so it matches the
// arrow that opened it. Volumes stay neutral A->B / B->A.
export default function StackPanel({ conn, stack, labelOpts, activeTier, lk, onBack }: Props) {
  const A = resolveLabel(labelOpts, conn.name_a, conn.whois_name_a, conn.ip_a);
  const B = resolveLabel(labelOpts, conn.name_b, conn.whois_name_b, conn.ip_b);
  const verShort = shortVersion(stack.protocol_version);
  const verOwner = versionOwnerToken(stack.protocol_version);

  const colour = edgeStyle(stack.layers, activeTier, lk).color;

  const portsA = useMemo<PortChip[]>(
    () => stack.ports_a.map((port) => ({ port, colour })),
    [stack.ports_a, colour]
  );
  const portsB = useMemo<PortChip[]>(
    () => stack.ports_b.map((port) => ({ port, colour })),
    [stack.ports_b, colour]
  );

  return (
    <Drawer onBack={onBack} backIcon="←">
      <h2>
        <span className="dot" style={{ background: nodeColor({ colour: conn.colour_a }) }} />
        {A}
        <span className="conv-arrow">↔</span>
        {B}
        <span className="dot" style={{ background: nodeColor({ colour: conn.colour_b }) }} />
      </h2>

      <div className="badges">
        <span className="badge">{fmtNum(stack.flow_count)} flows</span>
      </div>

      <h3>Protocol stack</h3>
      <div className="flow-stack">
        {stack.layers.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          stack.layers.map((tok, i) => {
            const unresolved = isUnresolved(tok, lk);
            const encrypted = isEncrypted(tok, lk);
            const showVer = verShort != null && tok === verOwner;
            const tokColour = lk.map.get(tok)?.colour ?? NEUTRAL_EDGE;
            const title = unresolved
              ? "dissection stopped — payload not identified"
              : encrypted
              ? `encrypted — payload sealed${stack.protocol_version ? ` (${stack.protocol_version})` : ""}`
              : showVer
              ? `${tok} ${stack.protocol_version}`
              : tok;
            return (
              <span key={`${tok}-${i}`} className="flow-step">
                {i > 0 && <span className="flow-arrow">›</span>}
                <span
                  className={`flow-chip${unresolved ? " unresolved" : ""}${
                    encrypted ? " encrypted" : ""
                  }`}
                  style={{ borderColor: tokColour }}
                  title={title}
                >
                  <span className="flow-chip-dot" style={{ background: tokColour }} />
                  {tok}
                  {showVer && <span className="flow-chip-ver">· {verShort}</span>}
                  {encrypted && <span className="flow-chip-lock">🔒</span>}
                </span>
              </span>
            );
          })
        )}
      </div>

      <h3>Ports in use</h3>
      <div className="proto-hint muted">
        Every {stack.l4_proto} port observed on each side for this stack.
      </div>
      <div className="port-cols">
        <PortColumn label={A} ports={portsA} />
        <PortColumn label={B} ports={portsB} />
      </div>

      <table className="kv">
        <tbody>
          <tr>
            <td>{A} → {B}</td>
            <td>{fmtNum(stack.pkts_a2b)} pkts · {fmtBytes(stack.bytes_a2b)}</td>
          </tr>
          <tr>
            <td>{B} → {A}</td>
            <td>{fmtNum(stack.pkts_b2a)} pkts · {fmtBytes(stack.bytes_b2a)}</td>
          </tr>
          <tr>
            <td>First seen</td>
            <td>{fmtTime(stack.first_seen)}</td>
          </tr>
          <tr>
            <td>Last seen</td>
            <td>{fmtTime(stack.last_seen)}</td>
          </tr>
        </tbody>
      </table>
    </Drawer>
  );
}
