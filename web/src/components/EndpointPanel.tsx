import type { LabelOpts, NodeT } from "../types";
import { fmtBytes, fmtNum, fmtTime, nodeColor, resolveLabel } from "../format";
import Drawer from "./Drawer";

interface Props {
  data: NodeT;
  labelOpts: LabelOpts;
  onBack: () => void;
}

// Endpoint detail drawer (node focus). kind chip only -- no local/remote. The heading
// honours the label toggles (given name from names.csv, else RDAP WHOIS name, else IP),
// with the raw IP shown beneath whenever a name is displayed. Both name sources are
// read-only here.
export default function EndpointPanel({ data, labelOpts, onBack }: Props) {
  const heading = resolveLabel(labelOpts, data.given_name, data.whois_name, data.ip);
  return (
    <Drawer onBack={onBack} backIcon="✕">
      <h2>
        <span className="dot" style={{ background: nodeColor(data) }} />
        {heading}
      </h2>
      {heading !== data.ip && <div className="conv-dir muted">{data.ip}</div>}
      <div className="badges">
        {/* `kind` is authoritative: the API already reports 'broadcast' for a VLAN's
            directed broadcast (.255), not the DB's raw 'unicast'. */}
        <span className="badge">{data.kind}</span>
        <span className="badge">{data.category_label}</span>
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
