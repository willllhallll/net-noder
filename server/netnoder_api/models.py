"""Pydantic response models (also drive the OpenAPI schema at /docs)."""
from typing import Optional

from pydantic import BaseModel


class Stats(BaseModel):
    endpoints: int
    conversations: int
    services: int
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


class EdgeService(BaseModel):
    proto: str
    port: Optional[int] = None
    cast: str


class Edge(BaseModel):
    id: int
    source: str
    target: str
    pkts: int
    bytes: int
    cast: str
    services: list[EdgeService] = []
    extra: int = 0  # count of services beyond those listed inline


class Graph(BaseModel):
    nodes: list[Node]
    edges: list[Edge]


class NodeDetail(Node):
    degree: int


class Service(BaseModel):
    l4_proto: str
    server_port: Optional[int] = None
    cast_type: str
    pkts: int
    bytes: int
    client_port_count: int = 0  # distinct ephemeral ports for this service


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


class ConversationDetail(BaseModel):
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
    services: list[Service]
