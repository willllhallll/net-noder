"""SQL expression fragments used during extraction + aggregation.

Kept as reusable strings so the (vectorised, C++-speed) work lives in DuckDB rather
than a per-packet Python loop. The peer/dissector model means there is deliberately
NO role logic, NO ephemeral-port logic, and NO IANA port map here -- only:

  * PROTO_SQL  - the L4 transport token from TCP/UDP presence or the IP proto number.
  * KIND_SQL   - unicast / multicast / broadcast from well-known IP ranges (the only
                 classification an endpoint gets; there is no local/remote judgement).
  * the protocol-stack explode + the generic-token strip set.
"""

# Layer-4 transport token, lower-cased to line up with the frame.protocols tokens.
# TCP/UDP from the dissected presence flags; everything else from the IP proto
# number. Portless L4s (icmp, gre, ...) get a name but no port.
PROTO_SQL = """
CASE
  WHEN is_tcp THEN 'tcp'
  WHEN is_udp THEN 'udp'
  WHEN proto_num = '1'   THEN 'icmp'
  WHEN proto_num = '2'   THEN 'igmp'
  WHEN proto_num = '58'  THEN 'icmpv6'
  WHEN proto_num = '47'  THEN 'gre'
  WHEN proto_num = '50'  THEN 'esp'
  WHEN proto_num = '51'  THEN 'ah'
  WHEN proto_num = '89'  THEN 'ospf'
  WHEN proto_num = '132' THEN 'sctp'
  WHEN proto_num IS NOT NULL THEN 'ip/' || proto_num
  ELSE 'other'
END
"""


def kind_sql(ip_col: str) -> str:
    """Classify an IP string as unicast / multicast / broadcast by its own value.

    IPv4 broadcast is the all-ones address; IPv4 multicast is 224.0.0.0/4
    (first octet 224-239); IPv6 multicast is ff00::/8. Everything else is unicast.
    No local/remote distinction is made.
    """
    return f"""
CASE
  WHEN {ip_col} = '255.255.255.255' THEN 'broadcast'
  WHEN contains({ip_col}, '.')
       AND TRY_CAST(split_part({ip_col}, '.', 1) AS INTEGER) BETWEEN 224 AND 239
       THEN 'multicast'
  WHEN contains({ip_col}, ':') AND lower({ip_col}) LIKE 'ff%' THEN 'multicast'
  ELSE 'unicast'
END
"""


# Pure framing/link/network tokens that carry no protocol insight on the edge view.
# Stripped only in connection_protocols (the edge-click drawer) so it reads as the
# real application/transport protocols; flow_layers keeps the full stack for fidelity
# (minus the 'ethertype' noise token, which is dropped at explode time).
GENERIC_LAYERS = (
    "eth", "ethertype", "sll", "sll2", "llc", "vlan",
    "ip", "ipv6",
)

# The single noise token dropped from flow_layers entirely: it is the EtherType
# field pseudo-layer, never a protocol anyone names, and keeping it would shift the
# layer_index of every following token (breaking the modal-index tier fallback).
NOISE_TOKEN = "ethertype"


def generic_layers_sql() -> str:
    """A SQL ``(...)`` tuple literal of the generic layer tokens, for ``NOT IN``."""
    return "(" + ", ".join(f"'{t}'" for t in GENERIC_LAYERS) + ")"
