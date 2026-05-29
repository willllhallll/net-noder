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

// Node colour by role: purple multicast, red broadcast, else blue local / orange
// external. Mirrors the legend in App.tsx; keep the two in sync.
export function nodeColor(kind: string, isLocal: boolean): string {
  if (kind === "multicast") return "#a855f7";
  if (kind === "broadcast") return "#ef4444";
  return isLocal ? "#3b82f6" : "#f59e0b";
}

// Edge colour flags a multicast/broadcast connection; ordinary unicast is grey.
export function castColor(cast: string): string {
  if (cast === "multicast") return "#a855f7";
  if (cast === "broadcast") return "#ef4444";
  return "#64748b";
}

// Node diameter and edge width scale with the log of bytes (traffic spans many
// orders of magnitude) and are clamped so the busiest node/edge can't dominate the
// canvas and the quietest stays visible.
export function nodeSize(bytes: number): number {
  return Math.max(10, Math.min(60, 8 + 7 * Math.log10(bytes + 10)));
}

export function edgeWidth(bytes: number): number {
  return Math.max(1, Math.min(12, 0.5 + 1.6 * Math.log10(bytes + 10)));
}

import type { Conversation, ConnectionDetail } from "./types";

// Label for one conversation: "tcp/443", or just the proto when no service port.
export function convLabel(c: { l4_proto: string; server_port: number | null }): string {
  return c.server_port == null
    ? c.l4_proto.toLowerCase()
    : `${c.l4_proto.toLowerCase()}/${c.server_port}`;
}

export interface ConvDirection {
  // null when there's no service port (undirected).
  client: string | null;
  server: string | null;
  // Bytes/pkts split by role; falls back to a<->b when undirected.
  c2sBytes: number;
  s2cBytes: number;
  c2sPkts: number;
  s2cPkts: number;
}

// Resolve client/server endpoints + per-direction volume for a conversation,
// using server_is_a. server_is_a===true => server is ip_a, client is ip_b, so
// client->server is the b2a flow; ===false flips it; null => undirected.
export function convDirection(
  conn: Pick<ConnectionDetail, "ip_a" | "ip_b">,
  c: Conversation
): ConvDirection {
  if (c.server_is_a === true) {
    return {
      client: conn.ip_b,
      server: conn.ip_a,
      c2sBytes: c.bytes_b2a,
      s2cBytes: c.bytes_a2b,
      c2sPkts: c.pkts_b2a,
      s2cPkts: c.pkts_a2b,
    };
  }
  if (c.server_is_a === false) {
    return {
      client: conn.ip_a,
      server: conn.ip_b,
      c2sBytes: c.bytes_a2b,
      s2cBytes: c.bytes_b2a,
      c2sPkts: c.pkts_a2b,
      s2cPkts: c.pkts_b2a,
    };
  }
  // Undirected: no server port. Report raw a->b / b->a volumes.
  return {
    client: null,
    server: null,
    c2sBytes: c.bytes_a2b,
    s2cBytes: c.bytes_b2a,
    c2sPkts: c.pkts_a2b,
    s2cPkts: c.pkts_b2a,
  };
}
