// Peer/dissector model: no roles, no arrows, no IANA services, no local/remote.
// Direction is surfaced only as neutral A->B / B->A counters.

export interface Stats {
  endpoints: number;
  connections: number;
  flows: number;
  layers: number;
  total_pkts: number;
  total_bytes: number;
  first_seen: number | null;
  last_seen: number | null;
}

export type Tier = "link" | "network" | "transport" | "application";

// One observed protocol token with its persisted tier + colour and usage count.
export interface Layer {
  layer: string;
  tier: Tier;
  colour: string;
  count: number;
  unresolved: boolean; // true for tshark stop-markers ('data'), not real protocols
}

export type LabelMode = "ip" | "name";

export interface NodeT {
  ip: string;
  kind: string; // 'unicast' | 'multicast' | 'broadcast'
  given_name: string | null;
  total_pkts: number;
  total_bytes: number;
  degree: number;
  first_seen: number | null;
  last_seen: number | null;
}

// An undirected peer edge. `layers` is the full token set present on the edge.
export interface EdgeT {
  connection_id: number;
  ip_a: string;
  ip_b: string;
  pkts: number;
  bytes: number;
  pkts_a2b: number;
  bytes_a2b: number;
  pkts_b2a: number;
  bytes_b2a: number;
  layers: string[];
  flow_count: number; // distinct flows (5-tuples) shared by the pair
  first_seen: number | null;
  last_seen: number | null;
}

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

// One canonical 5-tuple: the L4 proto + its two ports, plus the full dissected
// stack (`layers`, ordered from eth up). Direction is neutral A->B / B->A. This is
// the unit of drill-down — one flow becomes one arrow in the flow-fan view.
export interface Flow {
  flow_id: number;
  l4_proto: string;
  port_a: number | null; // port on ip_a side (null for portless L4, e.g. icmp)
  port_b: number | null;
  pkts_a2b: number;
  bytes_a2b: number;
  pkts_b2a: number;
  bytes_b2a: number;
  layers: string[]; // ordered stack: ['eth','ip','tcp','tls',...]
  first_seen: number | null;
  last_seen: number | null;
}

// Edge-click payload: the pair summary + every flow between the two endpoints.
export interface ConnectionFlows {
  connection_id: number;
  ip_a: string;
  ip_b: string;
  name_a: string | null;
  name_b: string | null;
  kind_a: string;
  kind_b: string;
  pkts_a2b: number;
  bytes_a2b: number;
  pkts_b2a: number;
  bytes_b2a: number;
  flow_count: number;
  flows: Flow[];
}
