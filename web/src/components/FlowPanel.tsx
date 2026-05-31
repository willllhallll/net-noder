import type { ConnectionFlows, Flow, LabelMode } from "../types";
import type { LayerLookup } from "../format";
import {
  fmtBytes,
  fmtNum,
  fmtTime,
  isUnresolved,
  mostSpecific,
  NEUTRAL_EDGE,
} from "../format";
import Drawer from "./Drawer";

interface Props {
  conn: ConnectionFlows;
  flow: Flow;
  labelMode: LabelMode;
  lk: LayerLookup;
  onBack: () => void;
}

// Single-flow drawer: the FULL dissected stack of one 5-tuple as a breadcrumb
// (eth › ip › tcp › tls …, deepest last), with the flow's two ports underneath,
// isolated per endpoint with no notion of direction. Volumes stay neutral A->B /
// B->A. This is the only place the protocol stack is shown.
export default function FlowPanel({ conn, flow, labelMode, lk, onBack }: Props) {
  const label = (name: string | null, ip: string) =>
    labelMode === "name" && name ? name : ip;
  const A = label(conn.name_a, conn.ip_a);
  const B = label(conn.name_b, conn.ip_b);

  const deepest = mostSpecific(flow.layers, lk);
  const headColour = lk.map.get(deepest ?? "")?.colour ?? NEUTRAL_EDGE;

  return (
    <Drawer onBack={onBack} backIcon="←">
      <h2>
        <span className="dot" style={{ background: headColour }} />
        {A}
        <span className="conv-arrow">↔</span>
        {B}
      </h2>

      <h3>Protocol stack</h3>
      <div className="flow-stack">
        {flow.layers.length === 0 ? (
          <span className="muted">—</span>
        ) : (
          flow.layers.map((tok, i) => {
            const unresolved = isUnresolved(tok, lk);
            const colour = lk.map.get(tok)?.colour ?? NEUTRAL_EDGE;
            return (
              <span key={`${tok}-${i}`} className="flow-step">
                {i > 0 && <span className="flow-arrow">›</span>}
                <span
                  className={`flow-chip${unresolved ? " unresolved" : ""}`}
                  style={{ borderColor: colour }}
                  title={unresolved ? "dissection stopped — payload not identified" : tok}
                >
                  <span className="flow-chip-dot" style={{ background: colour }} />
                  {tok}
                </span>
              </span>
            );
          })
        )}
      </div>

      <h3>Ports</h3>
      <div className="flow-ports">
        <div className="flow-port">
          <span className="muted">{A}</span>
          <span className="flow-port-num">
            {flow.port_a != null ? `:${flow.port_a}` : "—"}
          </span>
        </div>
        <div className="flow-port-sep">·{flow.l4_proto}·</div>
        <div className="flow-port">
          <span className="muted">{B}</span>
          <span className="flow-port-num">
            {flow.port_b != null ? `:${flow.port_b}` : "—"}
          </span>
        </div>
      </div>

      <table className="kv">
        <tbody>
          <tr>
            <td>{A} → {B}</td>
            <td>{fmtNum(flow.pkts_a2b)} pkts · {fmtBytes(flow.bytes_a2b)}</td>
          </tr>
          <tr>
            <td>{B} → {A}</td>
            <td>{fmtNum(flow.pkts_b2a)} pkts · {fmtBytes(flow.bytes_b2a)}</td>
          </tr>
          <tr>
            <td>First seen</td>
            <td>{fmtTime(flow.first_seen)}</td>
          </tr>
          <tr>
            <td>Last seen</td>
            <td>{fmtTime(flow.last_seen)}</td>
          </tr>
        </tbody>
      </table>
    </Drawer>
  );
}
