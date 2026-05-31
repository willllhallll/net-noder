import { useMemo } from "react";
import type { ConnectionFlows, LabelMode } from "../types";
import { fmtBytes, fmtNum, nodeColor } from "../format";
import Drawer from "./Drawer";

interface Props {
  conn: ConnectionFlows;
  labelMode: LabelMode;
  onBack: () => void;
}

// Distinct, sorted ports observed on one side of the 5-tuples (peer-to-peer: just
// "the ports this endpoint used", no direction, no IANA service interpretation).
function distinctPorts(ports: (number | null)[]): number[] {
  return Array.from(
    new Set(ports.filter((p): p is number => p != null))
  ).sort((a, b) => a - b);
}

// The intermediate flows-summary drawer (edge clicked, no individual flow chosen
// yet): the A <-> B pair with neutral packet/byte utilisation, plus every 5-tuple
// port in use on each side. The protocol stack is deliberately omitted here — the
// user explores that by clicking individual flow arrows.
export default function ConnectionPanel({ conn, labelMode, onBack }: Props) {
  const label = (name: string | null, ip: string) =>
    labelMode === "name" && name ? name : ip;
  const A = label(conn.name_a, conn.ip_a);
  const B = label(conn.name_b, conn.ip_b);

  const portsA = useMemo(
    () => distinctPorts(conn.flows.map((f) => f.port_a)),
    [conn.flows]
  );
  const portsB = useMemo(
    () => distinctPorts(conn.flows.map((f) => f.port_b)),
    [conn.flows]
  );

  return (
    <Drawer onBack={onBack} backIcon="✕">
      <h2>
        <span className="dot" style={{ background: nodeColor(conn.kind_a) }} />
        {A}
        <span className="conv-arrow">↔</span>
        {B}
        <span className="dot" style={{ background: nodeColor(conn.kind_b) }} />
      </h2>

      <div className="badges">
        <span className="badge">{fmtNum(conn.flow_count)} flows</span>
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
        Every 5-tuple port observed on each side. Click a flow arrow for its full
        stack &amp; the exact port pair.
      </div>
      <div className="port-cols">
        <PortColumn label={A} ports={portsA} />
        <PortColumn label={B} ports={portsB} />
      </div>
    </Drawer>
  );
}

function PortColumn({ label, ports }: { label: string; ports: number[] }) {
  return (
    <div className="port-col">
      <div className="port-col-head">
        {label} <span className="muted">({fmtNum(ports.length)})</span>
      </div>
      <div className="port-col-body">
        {ports.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          ports.map((p) => (
            <span key={p} className="port-chip">:{p}</span>
          ))
        )}
      </div>
    </div>
  );
}
