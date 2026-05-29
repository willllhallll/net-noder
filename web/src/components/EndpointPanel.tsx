import type { NodeDetail } from "../types";
import { fmtBytes, fmtNum, fmtTime, nodeColor } from "../format";
import Drawer from "./Drawer";

interface Props {
  data: NodeDetail;
  // Exits the focused view and returns to the unfiltered host graph.
  onBack: () => void;
}

// Endpoint detail drawer, shown while the graph is filtered to this endpoint's
// connections. Its ✕ button is the only way back to the full graph.
export default function EndpointPanel({ data, onBack }: Props) {
  return (
    <Drawer onBack={onBack} backIcon="✕">
      <h2>
        <span
          className="dot"
          style={{ background: nodeColor(data.kind, data.is_local) }}
        />
        {data.given_name ?? data.ip}
      </h2>
      {data.given_name && <div className="conv-dir muted">{data.ip}</div>}
      <div className="badges">
        <span className="badge">{data.kind}</span>
        <span className="badge">{data.is_local ? "local" : "external"}</span>
        <span className="badge">{data.degree} peers</span>
      </div>
      <table className="kv">
        <tbody>
          <tr>
            <td>Packets</td>
            <td>{fmtNum(data.total_pkts)}</td>
          </tr>
          <tr>
            <td>Bytes</td>
            <td>{fmtBytes(data.total_bytes)}</td>
          </tr>
          <tr>
            <td>First seen</td>
            <td>{fmtTime(data.first_seen)}</td>
          </tr>
          <tr>
            <td>Last seen</td>
            <td>{fmtTime(data.last_seen)}</td>
          </tr>
        </tbody>
      </table>
    </Drawer>
  );
}
