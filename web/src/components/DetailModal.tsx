import { Fragment, useState } from "react";
import { api } from "../api";
import type {
  ConversationDetail,
  EphemeralPorts,
  NodeDetail,
  Service,
} from "../types";
import { castColor, fmtBytes, fmtNum, fmtTime, nodeColor } from "../format";

export type ModalContent =
  | { type: "node"; data: NodeDetail }
  | { type: "conversation"; data: ConversationDetail };

interface Props {
  content: ModalContent;
  onClose: () => void;
  onExpand: (ip: string) => void;
}

export default function DetailModal({ content, onClose, onExpand }: Props) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <button className="modal-close" onClick={onClose}>
          ×
        </button>
        {content.type === "node" ? (
          <NodeBody data={content.data} onExpand={onExpand} />
        ) : (
          <ConvBody data={content.data} onExpand={onExpand} />
        )}
      </div>
    </div>
  );
}

function NodeBody({
  data,
  onExpand,
}: {
  data: NodeDetail;
  onExpand: (ip: string) => void;
}) {
  return (
    <>
      <h2>
        <span
          className="dot"
          style={{ background: nodeColor(data.kind, data.is_local) }}
        />
        {data.given_name ?? data.ip}
      </h2>
      {data.given_name && <div className="modal-sub muted">{data.ip}</div>}
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
      <button className="btn" onClick={() => onExpand(data.ip)}>
        Expand neighbours
      </button>
    </>
  );
}

function ConvBody({
  data,
  onExpand,
}: {
  data: ConversationDetail;
  onExpand: (ip: string) => void;
}) {
  return (
    <>
      <div className="conv-title">
        <ConvHost name={data.name_a} ip={data.ip_a} />
        <span className="conv-sep muted">↔</span>
        <ConvHost name={data.name_b} ip={data.ip_b} />
      </div>
      <table className="kv">
        <tbody>
          <tr>
            <td>{data.ip_a} → {data.ip_b}</td>
            <td>
              {fmtNum(data.pkts_a2b)} pkts · {fmtBytes(data.bytes_a2b)}
            </td>
          </tr>
          <tr>
            <td>{data.ip_b} → {data.ip_a}</td>
            <td>
              {fmtNum(data.pkts_b2a)} pkts · {fmtBytes(data.bytes_b2a)}
            </td>
          </tr>
          <tr>
            <td>Window</td>
            <td>
              {fmtTime(data.first_seen)} – {fmtTime(data.last_seen)}
            </td>
          </tr>
        </tbody>
      </table>
      <h3>Services</h3>
      <ServicesTable conv={data} />
      <div className="row-gap">
        <button className="btn" onClick={() => onExpand(data.ip_a)}>
          Expand {data.ip_a}
        </button>
        <button className="btn" onClick={() => onExpand(data.ip_b)}>
          Expand {data.ip_b}
        </button>
      </div>
    </>
  );
}

// One side of a conversation: given name as title, IP as subtitle. When no name is
// known, the IP is shown in both places.
function ConvHost({ name, ip }: { name: string | null; ip: string }) {
  return (
    <div className="conv-host">
      <div className="conv-host-name">{name ?? ip}</div>
      <div className="conv-host-ip muted">{ip}</div>
    </div>
  );
}

type RowState = EphemeralPorts | "loading" | undefined;

function ServicesTable({ conv }: { conv: ConversationDetail }) {
  const [open, setOpen] = useState<Record<number, RowState>>({});

  async function toggle(i: number, s: Service) {
    if (open[i]) {
      setOpen((p) => ({ ...p, [i]: undefined }));
      return;
    }
    setOpen((p) => ({ ...p, [i]: "loading" }));
    try {
      const d = await api.ephemeralPorts(
        conv.ip_a,
        conv.ip_b,
        s.l4_proto,
        s.server_port as number,
        s.cast_type
      );
      setOpen((p) => ({ ...p, [i]: d }));
    } catch {
      setOpen((p) => ({ ...p, [i]: undefined }));
    }
  }

  return (
    <table className="services">
      <thead>
        <tr>
          <th>Proto</th>
          <th>Port</th>
          <th>Cast</th>
          <th>Packets</th>
          <th>Bytes</th>
          <th>Ephemeral</th>
        </tr>
      </thead>
      <tbody>
        {conv.services.map((s, i) => {
          const canView = s.server_port != null && s.client_port_count > 0;
          const state = open[i];
          return (
            <Fragment key={i}>
              <tr>
                <td>{s.l4_proto}</td>
                <td>{s.server_port ?? "—"}</td>
                <td>
                  <span
                    className="dot"
                    style={{ background: castColor(s.cast_type) }}
                  />
                  {s.cast_type}
                </td>
                <td>{fmtNum(s.pkts)}</td>
                <td>{fmtBytes(s.bytes)}</td>
                <td>
                  {canView ? (
                    <button className="link-btn" onClick={() => toggle(i, s)}>
                      {state ? "hide" : `view (${s.client_port_count})`}
                    </button>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
              {state && (
                <tr className="ephemeral-row">
                  <td colSpan={6}>
                    {state === "loading" ? (
                      <span className="muted">loading…</span>
                    ) : (
                      <EphemeralList data={state} />
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

function EphemeralList({ data }: { data: EphemeralPorts }) {
  return (
    <div className="ephemeral">
      <div className="ephemeral-head muted">
        {data.total} ephemeral port{data.total === 1 ? "" : "s"} talking to{" "}
        {data.l4_proto}/{data.server_port}
        {data.truncated ? ` · showing top ${data.ports.length} by bytes` : ""}
      </div>
      <div className="chips">
        {data.ports.map((p) => (
          <span
            className="chip"
            key={p.port}
            title={`${fmtNum(p.pkts)} pkts · ${fmtBytes(p.bytes)}`}
          >
            {p.port}
            <span className="chip-sub">{fmtBytes(p.bytes)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
