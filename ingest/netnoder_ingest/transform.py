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

# Well-known/registered service ports used to disambiguate which side is the server.
_WELLKNOWN = (
    "(20,21,22,23,25,53,67,68,69,80,110,123,135,137,138,139,143,161,162,179,389,"
    "443,445,465,514,515,587,631,636,853,873,989,990,993,995,1080,1194,1433,1521,"
    "1883,1900,2049,3128,3306,3389,5060,5061,5353,5432,5672,5900,6379,8080,8443,"
    "9000,9090,9092,9200,11211,27017)"
)

# Pick the "server" port: prefer a well-known port, else the lower port number.
SERVER_PORT_SQL = f"""
CASE
  WHEN src_port IS NULL AND dst_port IS NULL THEN NULL
  WHEN src_port IS NULL THEN dst_port
  WHEN dst_port IS NULL THEN src_port
  WHEN dst_port IN {_WELLKNOWN} AND src_port NOT IN {_WELLKNOWN} THEN dst_port
  WHEN src_port IN {_WELLKNOWN} AND dst_port NOT IN {_WELLKNOWN} THEN src_port
  ELSE LEAST(src_port, dst_port)
END
"""
