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


class Node(BaseModel):
    ip: str
    kind: str  # 'unicast' | 'multicast' | 'broadcast'
    given_name: Optional[str] = None
    total_pkts: int
    total_bytes: int
    degree: int
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


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


class Flow(BaseModel):
    """One canonical 5-tuple: l4 proto + the two ports, with its full dissected stack.

    `layers` is the ordered protocol stack (eth up). Direction is neutral A->B / B->A.
    """
    flow_id: int
    l4_proto: str
    port_a: Optional[int] = None  # port on ip_a side (NULL for portless L4 e.g. icmp)
    port_b: Optional[int] = None
    pkts_a2b: int
    bytes_a2b: int
    pkts_b2a: int
    bytes_b2a: int
    layers: list[str] = []
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


class ConnectionFlows(BaseModel):
    """Edge-click view: the pair summary + every flow between the two endpoints."""
    connection_id: int
    ip_a: str
    ip_b: str
    name_a: Optional[str] = None
    name_b: Optional[str] = None
    kind_a: str
    kind_b: str
    pkts_a2b: int
    bytes_a2b: int
    pkts_b2a: int
    bytes_b2a: int
    flow_count: int
    flows: list[Flow]
