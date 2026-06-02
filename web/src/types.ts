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
  encrypted: boolean; // true for the TLS/SSL session boundary (sealed payload, 🔒)
}

// The two independent label sources that can each be layered onto a node. Either can be
// on/off; when both are on, the user given-name wins over the machine RDAP name, and both
// fall back to the raw IP (see resolveLabel in format.ts).
export interface LabelOpts {
  given: boolean; // show user-curated given names
  whois: boolean; // show RDAP-resolved WHOIS names
}

export interface NodeT {
  ip: string;
  kind: string; // 'unicast' | 'multicast' | 'broadcast'
  given_name: string | null;
  whois_name: string | null; // RDAP-resolved org/netblock handle, or null
  total_pkts: number;
  total_bytes: number;
  degree: number;
  first_seen: number | null;
  last_seen: number | null;
  category: string; // broadcast domain: 'vlan_<id>' | 'public' | 'unassigned' | 'multicast' | 'broadcast'
  category_label: string; // friendly label, e.g. "VLAN 200" or "VLAN 200 (broadcast)"
  vlan_id: number | null;
  colour: string; // broadcast-domain colour from broadcast_domain_colours
}

// One broadcast domain (a VLAN subnet or a fixed bucket) for the hosts-view filter.
export interface Category {
  category_key: string;
  label: string;
  colour: string;
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

// The flows of one connection that share an identical full stack, collapsed into one.
// `layers` (ordered eth up) is common to every flow in the group; one stack becomes one
// arrow in the flow-fan view. `ports_a`/`ports_b` are the distinct ports each side used
// across those flows — this is where port churn lives, not in a fan of edges.
export interface ConnectionStack {
  layers: string[]; // de-noised ordered stack: ['eth','ip','tcp','tls'] (sealed content cut)
  l4_proto: string;
  protocol_version: string | null; // decoded version label of the principal protocol (TLS/QUIC/HTTP/…), or null
  flow_count: number; // how many 5-tuples collapsed into this stack
  pkts_a2b: number;
  bytes_a2b: number;
  pkts_b2a: number;
  bytes_b2a: number;
  ports_a: number[]; // distinct ports on ip_a side (empty for portless L4, e.g. icmp)
  ports_b: number[];
  first_seen: number | null;
  last_seen: number | null;
}

// Edge-click payload: the pair summary + its flows grouped by identical stack.
export interface ConnectionStacks {
  connection_id: number;
  ip_a: string;
  ip_b: string;
  name_a: string | null;
  name_b: string | null;
  whois_name_a: string | null; // RDAP-resolved name for ip_a, or null
  whois_name_b: string | null;
  kind_a: string;
  kind_b: string;
  colour_a: string; // category colour of ip_a (matches the base-graph node colour)
  colour_b: string;
  pkts_a2b: number;
  bytes_a2b: number;
  pkts_b2a: number;
  bytes_b2a: number;
  flow_count: number;
  stacks: ConnectionStack[];
}
