import type {
  Conversation,
  ConnectionDetail,
  ReplyPorts,
  LabelMode,
} from "../types";
import {
  castColor,
  convDirection,
  convLabel,
  fmtBytes,
  fmtNum,
  fmtTime,
} from "../format";
import Drawer from "./Drawer";

type ConvPorts = ReplyPorts | "loading" | null;

interface Props {
  conn: ConnectionDetail;
  // The selected conversation, or null before one is tapped.
  conv: Conversation | null;
  ports: ConvPorts;
  labelMode: LabelMode;
  // Steps back to where the drill-down was entered from (focus or hosts).
  onBack: () => void;
}

// Connection drill-down drawer: shows the A ↔ B pair (formerly the breadcrumb)
// and, once a conversation edge is tapped, that conversation's details. Its ←
// button is the single, fixed control to step back up a layer.
export default function ConnectionPanel({
  conn,
  conv,
  ports,
  labelMode,
  onBack,
}: Props) {
  const label = (name: string | null, ip: string) =>
    labelMode === "name" && name ? name : ip;

  return (
    <Drawer onBack={onBack} backIcon="←">
      <h2>
        {label(conn.name_a, conn.ip_a)} ↔ {label(conn.name_b, conn.ip_b)}
      </h2>
      {conv ? (
        <ConversationBody conn={conn} conv={conv} ports={ports} />
      ) : (
        <div className="conv-dir muted">click a conversation edge for details</div>
      )}
    </Drawer>
  );
}

// Details of one conversation: client/server identification, per-direction
// volume, and the reply ports.
function ConversationBody({
  conn,
  conv,
  ports,
}: {
  conn: ConnectionDetail;
  conv: Conversation;
  ports: ConvPorts;
}) {
  const dir = convDirection(conn, conv);
  const directed = conv.server_is_a != null;
  const name = (ip: string | null) =>
    ip === conn.ip_a ? conn.name_a ?? ip : ip === conn.ip_b ? conn.name_b ?? ip : ip;

  return (
    <>
      <h3>
        <span className="dot" style={{ background: castColor(conv.cast_type) }} />
        {convLabel(conv)}
      </h3>
      <div className="badges">
        <span className="badge">{conv.l4_proto}</span>
        <span className="badge">{conv.cast_type}</span>
        {conv.reply_port_count > 0 && (
          <span className="badge">
            {conv.reply_port_count} reply port{conv.reply_port_count === 1 ? "" : "s"}
          </span>
        )}
      </div>

      {directed ? (
        <div className="conv-dir">
          <span className="conv-role">{name(dir.client)}</span>
          <span className="conv-arrow">→</span>
          <span className="conv-role conv-role-server">{name(dir.server)}</span>
        </div>
      ) : (
        <div className="conv-dir muted">no service port — direction unknown</div>
      )}

      <table className="kv">
        <tbody>
          <tr>
            <td>Service</td>
            <td>{conv.service ?? "No Service Info"}</td>
          </tr>
          <tr>
            <td>{directed ? "Client → Server" : `${conn.ip_a} → ${conn.ip_b}`}</td>
            <td>
              {fmtNum(dir.c2sPkts)} pkts · {fmtBytes(dir.c2sBytes)}
            </td>
          </tr>
          <tr>
            <td>{directed ? "Server → Client" : `${conn.ip_b} → ${conn.ip_a}`}</td>
            <td>
              {fmtNum(dir.s2cPkts)} pkts · {fmtBytes(dir.s2cBytes)}
            </td>
          </tr>
          <tr>
            <td>First seen</td>
            <td>{fmtTime(conv.first_seen)}</td>
          </tr>
          <tr>
            <td>Last seen</td>
            <td>{fmtTime(conv.last_seen)}</td>
          </tr>
        </tbody>
      </table>

      <h3>Reply ports</h3>
      {ports === "loading" ? (
        <span className="muted">loading…</span>
      ) : ports ? (
        <ReplyList data={ports} />
      ) : (
        <span className="muted">none recorded</span>
      )}
    </>
  );
}

function ReplyList({ data }: { data: ReplyPorts }) {
  return (
    <div className="reply">
      <div className="reply-head muted">
        {data.total} reply port{data.total === 1 ? "" : "s"} talking to{" "}
        {data.l4_proto}/{data.server_port}
        {data.truncated ? ` · showing top ${data.ports.length} by bytes` : ""}
      </div>
      <div className="chips">
        {data.ports.map((p) => (
          <span className="chip" key={p.port} title={`${fmtNum(p.pkts)} pkts`}>
            {p.port}
          </span>
        ))}
      </div>
    </div>
  );
}
