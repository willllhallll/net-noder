"""Pydantic response models (also drive the OpenAPI schema at /docs).

Peer/dissector model: no roles, no arrows, no IANA services, no local/remote. The
API serves four views -- graph, endpoint, connection protocols, protocol ports --
plus stats, the layer registry, search, and name editing. Direction is surfaced only
as neutral A->B / B->A counters; the human judges direction from the volumes.
"""
from typing import Optional

from pydantic import BaseModel


class Stats(BaseModel):
    endpoints: int
    connections: int
    flows: int
    layers: int
    total_pkts: int
    total_bytes: int
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


class Layer(BaseModel):
    """One observed protocol token with its persisted tier + colour and usage count."""
    layer: str
    tier: str
    colour: str
    count: int  # distinct connections carrying this layer
    unresolved: bool = False  # True for tshark stop-markers ('data'), not real protocols
    encrypted: bool = False   # True for the TLS/SSL session boundary (sealed payload, 🔒)


class Node(BaseModel):
    ip: str
    kind: str  # 'unicast' | 'multicast' | 'broadcast'
    given_name: Optional[str] = None
    whois_name: Optional[str] = None  # RDAP-resolved org/netblock handle (ip_whois)
    total_pkts: int
    total_bytes: int
    degree: int
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    # VLAN/subnet category, derived at query time from the `vlans` registry.
    category: str = "unassigned"
    category_label: str = "Unassigned"
    vlan_id: Optional[int] = None
    colour: str = "#60a5fa"  # category colour from vlan_colours (fallback = sky-blue)


class Category(BaseModel):
    """One endpoint colour category (a VLAN or a fixed bucket) for the filter legend."""
    category_key: str
    label: str
    colour: str


class Edge(BaseModel):
    """An undirected peer edge. layers = the full token set present (client colours/filters)."""
    connection_id: int
    ip_a: str
    ip_b: str
    pkts: int
    bytes: int
    pkts_a2b: int
    bytes_a2b: int
    pkts_b2a: int
    bytes_b2a: int
    layers: list[str] = []
    flow_count: int = 0  # distinct flows (5-tuples) shared by the pair
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


class GraphMeta(BaseModel):
    capped: bool
    cap: int
    shown_endpoints: int
    total_endpoints: int


class Graph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    meta: Optional[GraphMeta] = None


# Endpoint detail is just a node (which already carries degree).
EndpointDetail = Node


class ConnectionStack(BaseModel):
    """The flows of one connection that share an identical full stack, collapsed.

    `layers` is the ordered protocol stack (eth up) common to every flow in the group,
    de-noised by the encryption-boundary cut (tokens after an encrypted layer dropped when
    its version is known); `flow_count` is how many 5-tuples collapsed into it.
    `ports_a`/`ports_b` are the distinct ports each side used across those flows (this is
    where port churn lives). `protocol_version` is the decoded version label of the group's
    most-specific versioned protocol (TLS/DTLS/QUIC/HTTP/NTP), else null. Direction is
    neutral A->B / B->A.
    """
    layers: list[str] = []
    l4_proto: str
    protocol_version: Optional[str] = None
    flow_count: int
    pkts_a2b: int
    bytes_a2b: int
    pkts_b2a: int
    bytes_b2a: int
    ports_a: list[int] = []
    ports_b: list[int] = []
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


class ConnectionStacks(BaseModel):
    """Edge-click view: the pair summary + its flows grouped by identical stack."""
    connection_id: int
    ip_a: str
    ip_b: str
    name_a: Optional[str] = None
    name_b: Optional[str] = None
    whois_name_a: Optional[str] = None  # RDAP-resolved name for ip_a (ip_whois)
    whois_name_b: Optional[str] = None  # RDAP-resolved name for ip_b
    kind_a: str
    kind_b: str
    # Category colours (from vlan_colours), so the flow view paints its two endpoints the
    # same as the base graph instead of falling back to kind-based colours.
    colour_a: str = "#60a5fa"
    colour_b: str = "#60a5fa"
    pkts_a2b: int
    bytes_a2b: int
    pkts_b2a: int
    bytes_b2a: int
    flow_count: int
    stacks: list[ConnectionStack]
