"""SQL expression fragments that classify packets during aggregation.

Kept as reusable strings so the (vectorised, C++-speed) classification lives in
DuckDB rather than a per-packet Python loop. They operate on the normalised columns
produced by extract.py: src_ip, dst_ip, eth_dst, proto_num, src_port, dst_port,
is_tcp, is_udp.
"""

# Protocol name from TCP/UDP presence, else the IP protocol number.
PROTO_SQL = """
CASE
  WHEN is_tcp THEN 'TCP'
  WHEN is_udp THEN 'UDP'
  WHEN proto_num = '1'   THEN 'ICMP'
  WHEN proto_num = '2'   THEN 'IGMP'
  WHEN proto_num = '58'  THEN 'ICMPv6'
  WHEN proto_num = '47'  THEN 'GRE'
  WHEN proto_num = '50'  THEN 'ESP'
  WHEN proto_num = '51'  THEN 'AH'
  WHEN proto_num = '89'  THEN 'OSPF'
  WHEN proto_num = '132' THEN 'SCTP'
  WHEN proto_num IS NOT NULL THEN 'IP/' || proto_num
  ELSE 'OTHER'
END
"""

# Cast type from the destination. The L2 group bit (odd first MAC octet) catches
# both IPv4 (01:00:5e) and IPv6 (33:33) multicast as well as broadcast (ff:..).
# IP-based fallbacks cover L3-only captures with no Ethernet header.
CAST_SQL = """
CASE
  WHEN eth_dst = 'ff:ff:ff:ff:ff:ff' OR dst_ip = '255.255.255.255' THEN 'broadcast'
  WHEN eth_dst IS NOT NULL
       AND right(split_part(eth_dst, ':', 1), 1) IN ('1','3','5','7','9','b','d','f')
       THEN 'multicast'
  WHEN TRY_CAST(split_part(dst_ip, '.', 1) AS INTEGER) BETWEEN 224 AND 239 THEN 'multicast'
  WHEN contains(dst_ip, ':') AND lower(dst_ip) LIKE 'ff%' THEN 'multicast'
  ELSE 'unicast'
END
"""

# IANA dynamic/ephemeral port range. A port in this range is a throwaway client
# port; any other (non-null) port is treated as a service port. This is the sole
# signal for deciding which side of a flow is the server, and whether a flow is a
# normal client->server exchange or a bidirectional service-to-service one.
EPHEMERAL_MIN = 49152
EPHEMERAL_MAX = 65535


def _is_ephemeral(col: str) -> str:
    return f"({col} BETWEEN {EPHEMERAL_MIN} AND {EPHEMERAL_MAX})"


def _is_service(col: str) -> str:
    return f"({col} IS NOT NULL AND NOT {_is_ephemeral(col)})"


# Direction-independent canonical service side for the cases where both ends are
# equally (un)qualified: pick the lower port number, ties broken by the lower IP.
# Because it depends only on the unordered {(ip, port)} pair and never on which side
# happens to be src/dst, a packet and its reverse resolve to the SAME service
# endpoint. That keeps a bidirectional exchange in one conversation row (with both
# directions populated) instead of splitting it into two one-directional rows.
_CANONICAL_SERVICE_IS_SRC = """
CASE
  WHEN src_port < dst_port THEN TRUE
  WHEN src_port > dst_port THEN FALSE
  ELSE (src_ip <= dst_ip)
END
"""

# Per-packet: is the *service* endpoint this packet's source? Drives server_port,
# reply_port and server_is_a downstream. The cases:
#   * neither port (non-TCP/UDP)         -> NULL (no service, undirected)
#   * both ports are service ports       -> service = the canonical (lower) side.
#       This is the bidirectional service case: forward and reverse packets resolve
#       to the same endpoint, so the exchange stays one conversation carrying both
#       directions (rather than two rows each missing the reverse direction's bytes).
#   * exactly one is a service port      -> service = that side. Covers a normal
#       client->server packet (dst is the service) and its server->client reply
#       (src is the service); both resolve to the same endpoint -> one conversation.
#   * neither is a service port          -> service = the canonical (lower) side.
SERVICE_IS_SRC_SQL = f"""
CASE
  WHEN src_port IS NULL AND dst_port IS NULL THEN NULL
  WHEN src_port IS NULL THEN FALSE
  WHEN dst_port IS NULL THEN TRUE
  WHEN {_is_service('src_port')} AND {_is_service('dst_port')} THEN {_CANONICAL_SERVICE_IS_SRC}
  WHEN {_is_service('src_port')} THEN TRUE
  WHEN {_is_service('dst_port')} THEN FALSE
  ELSE {_CANONICAL_SERVICE_IS_SRC}
END
"""
