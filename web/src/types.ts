export interface Stats {
  endpoints: number;
  connections: number;
  conversations: number;
  total_pkts: number;
  total_bytes: number;
  first_seen: number | null;
  last_seen: number | null;
}

export interface NodeT {
  ip: string;
  total_pkts: number;
  total_bytes: number;
  first_seen: number | null;
  last_seen: number | null;
  is_local: boolean;
  kind: string;
  hostname: string | null;
  given_name: string | null;
}

export type LabelMode = "ip" | "name";

export interface NodeDetail extends NodeT {
  degree: number;
}

// Inline chip summarising one conversation on a connection edge.
export interface EdgeConversation {
  proto: string;
  port: number | null;
  cast: string;
}

// A connection edge: any traffic between two endpoints.
export interface EdgeT {
  id: number;
  source: string;
  target: string;
  pkts: number;
  bytes: number;
  cast: string;
  conversations: EdgeConversation[];
  extra: number;
}

// Host-view truncation info, present only on the full graph response. `capped`
// is true when the dataset exceeded the node cap and only the top endpoints show.
export interface GraphMeta {
  capped: boolean;
  cap: number;
  shown_endpoints: number;
  total_endpoints: number;
}

export interface Graph {
  nodes: NodeT[];
  edges: EdgeT[];
  meta?: GraphMeta;
}

// Traffic toward one service endpoint (proto + server port) within a connection.
export interface Conversation {
  l4_proto: string;
  server_port: number | null;
  cast_type: string;
  pkts_a2b: number;
  bytes_a2b: number;
  pkts_b2a: number;
  bytes_b2a: number;
  reply_port_count: number;
  // true: ip_a is the server; false: ip_b; null: no service port (undirected).
  server_is_a: boolean | null;
  // port_services description for server_port; null -> "No Service Info".
  service: string | null;
  first_seen: number | null;
  last_seen: number | null;
}

export interface ReplyPort {
  port: number;
  pkts: number;
  bytes: number;
}

export interface ReplyPorts {
  l4_proto: string;
  server_port: number;
  cast_type: string;
  total: number;
  truncated: boolean;
  ports: ReplyPort[];
}

export interface ConnectionDetail {
  id: number;
  ip_a: string;
  ip_b: string;
  name_a: string | null;
  name_b: string | null;
  pkts_a2b: number;
  bytes_a2b: number;
  pkts_b2a: number;
  bytes_b2a: number;
  first_seen: number | null;
  last_seen: number | null;
  conversations: Conversation[];
}
