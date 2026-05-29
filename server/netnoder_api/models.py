"""Pydantic response models (also drive the OpenAPI schema at /docs).

Three-layer model: endpoints (one IP), connections (any traffic between a pair of
endpoints), and conversations (traffic on a specific service/port within a
connection, which may fan out to many ephemeral reply ports).
"""
from typing import Optional

from pydantic import BaseModel


class Stats(BaseModel):
    endpoints: int
    connections: int
    conversations: int
    total_pkts: int
    total_bytes: int
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


class Node(BaseModel):
    ip: str
    total_pkts: int
    total_bytes: int
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    is_local: bool
    kind: str
    hostname: Optional[str] = None
    given_name: Optional[str] = None


class EdgeConversation(BaseModel):
    proto: str
    port: Optional[int] = None
    cast: str


class Edge(BaseModel):
    """A connection edge in the graph: any traffic between two endpoints."""
    id: int
    source: str
    target: str
    pkts: int
    bytes: int
    cast: str
    conversations: list[EdgeConversation] = []  # top conversations listed inline
    extra: int = 0  # count of conversations beyond those listed inline


class GraphMeta(BaseModel):
    """Host-view truncation info: set when the node cap (MAX_GRAPH_NODES) trips."""
    capped: bool
    cap: int
    shown_endpoints: int
    total_endpoints: int


class Graph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    meta: Optional[GraphMeta] = None  # only the full host graph sets this


class NodeDetail(Node):
    degree: int


class Conversation(BaseModel):
    """Traffic on one assumed service (proto + server port) within a connection."""
    l4_proto: str
    server_port: Optional[int] = None
    cast_type: str
    pkts_a2b: int
    bytes_a2b: int
    pkts_b2a: int
    bytes_b2a: int
    client_port_count: int = 0  # distinct ephemeral ports for this conversation
    server_is_a: Optional[bool] = None  # True: ip_a is the server; False: ip_b; None: no service port
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None


class EphemeralPort(BaseModel):
    port: int
    pkts: int
    bytes: int


class EphemeralPorts(BaseModel):
    l4_proto: str
    server_port: int
    cast_type: str
    total: int           # distinct ephemeral ports overall
    truncated: bool      # True if more exist than returned
    ports: list[EphemeralPort]


class ConnectionDetail(BaseModel):
    id: int
    ip_a: str
    ip_b: str
    name_a: Optional[str] = None
    name_b: Optional[str] = None
    pkts_a2b: int
    bytes_a2b: int
    pkts_b2a: int
    bytes_b2a: int
    first_seen: Optional[float] = None
    last_seen: Optional[float] = None
    conversations: list[Conversation]
