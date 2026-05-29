export interface Stats {
  endpoints: number;
  conversations: number;
  services: number;
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

export interface EdgeService {
  proto: string;
  port: number | null;
  cast: string;
}

export interface EdgeT {
  id: number;
  source: string;
  target: string;
  pkts: number;
  bytes: number;
  cast: string;
  services: EdgeService[];
  extra: number;
}

export interface Graph {
  nodes: NodeT[];
  edges: EdgeT[];
}

export interface Service {
  l4_proto: string;
  server_port: number | null;
  cast_type: string;
  pkts: number;
  bytes: number;
  client_port_count: number;
}

export interface EphemeralPort {
  port: number;
  pkts: number;
  bytes: number;
}

export interface EphemeralPorts {
  l4_proto: string;
  server_port: number;
  cast_type: string;
  total: number;
  truncated: boolean;
  ports: EphemeralPort[];
}

export interface ConversationDetail {
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
  services: Service[];
}

export type Metric = "bytes" | "pkts";
