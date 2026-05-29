#!/usr/bin/env python3
"""Write a deterministic classic-pcap file for testing the ingest pipeline.

Crafts Ethernet/IPv4 frames in three layers:
  * curated flows covering unicast (TCP+UDP), multicast (mDNS), and broadcast
    (DHCP) so cast-type classification and rollups can be asserted;
  * high-fanout conversations where one client hits a server from many ephemeral
    ports (beyond the top-50 cap, so the ephemeral-port drill-down + truncation
    are exercised);
  * ~50 extra endpoints with dummy connections, for a denser, more graph-like
    dataset.

Everything is seeded, so re-running produces byte-identical output.

Usage: python tools/make_sample_pcap.py [out.pcap]   (default: data/captures/sample.pcap)
"""
import random
import socket
import struct
import sys
from pathlib import Path


def mac(s: str) -> bytes:
    return bytes(int(x, 16) for x in s.split(":"))


def ip(s: str) -> bytes:
    return socket.inet_aton(s)


def ipv4(src: str, dst: str, proto: int, payload: bytes) -> bytes:
    total = 20 + len(payload)
    hdr = struct.pack(
        "!BBHHHBBH4s4s", 0x45, 0, total, 0x1234, 0, 64, proto, 0, ip(src), ip(dst)
    )
    return hdr + payload


def tcp(sport: int, dport: int, payload: bytes) -> bytes:
    return struct.pack(
        "!HHIIBBHHH", sport, dport, 0, 0, (5 << 4), 0x18, 65535, 0, 0
    ) + payload


def udp(sport: int, dport: int, payload: bytes) -> bytes:
    return struct.pack("!HHHH", sport, dport, 8 + len(payload), 0) + payload


def frame(eth_dst, eth_src, src, dst, proto, sport, dport, plen) -> bytes:
    payload = b"\x00" * plen
    if proto == 6:
        l4 = tcp(sport, dport, payload)
    elif proto == 17:
        l4 = udp(sport, dport, payload)
    else:
        l4 = payload
    eth = mac(eth_dst) + mac(eth_src) + struct.pack("!H", 0x0800)
    return eth + ipv4(src, dst, proto, l4)


LOCAL_A = "02:11:11:11:11:11"
LOCAL_B = "02:22:22:22:22:22"
GW = "02:33:33:33:33:33"
# A generic locally-administered *unicast* MAC for synthetic traffic (even first
# octet => unicast, so cast classification stays 'unicast').
UNI = "02:44:44:44:44:44"

# count, eth_dst, eth_src, src_ip, dst_ip, proto, sport, dport, payload_len
CURATED = [
    (5, GW, LOCAL_A, "192.168.1.10", "93.184.216.34", 6, 51514, 443, 500),    # HTTPS out
    (4, LOCAL_A, GW, "93.184.216.34", "192.168.1.10", 6, 443, 51514, 1400),   # HTTPS in
    # Same service (TCP/443) from two more ephemeral client ports.
    (3, GW, LOCAL_A, "192.168.1.10", "93.184.216.34", 6, 51515, 443, 480),
    (3, LOCAL_A, GW, "93.184.216.34", "192.168.1.10", 6, 443, 51515, 900),
    (2, GW, LOCAL_A, "192.168.1.10", "93.184.216.34", 6, 51516, 443, 480),
    (2, LOCAL_A, GW, "93.184.216.34", "192.168.1.10", 6, 443, 51516, 900),
    (2, GW, LOCAL_A, "192.168.1.10", "192.168.1.1", 17, 40000, 53, 40),       # DNS
    (3, "01:00:5e:00:00:fb", LOCAL_A, "192.168.1.10", "224.0.0.251", 17, 5353, 5353, 60),  # mDNS
    (1, "ff:ff:ff:ff:ff:ff", LOCAL_B, "192.168.1.50", "255.255.255.255", 17, 68, 67, 300), # DHCP
    # A single host pair with many conversations, to exercise multi-port drill-down.
    (6, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 6, 50001, 443, 1200),  # HTTPS
    (5, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 6, 50002, 80, 800),    # HTTP
    (3, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 6, 50003, 22, 200),    # SSH
    (4, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 17, 50004, 53, 90),    # DNS
    (2, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 17, 50005, 123, 76),   # NTP
]


def fanout_flows():
    """Conversations where a client hits a server on one port from many ephemeral
    ports. 70 ports > the top-50 cap, so client_port_count and ``truncated`` are
    exercised in the ephemeral-port view."""
    flows = []
    # (client_ip, server_ip, server_port, base_ephemeral_port, n_ports)
    cases = [
        ("192.168.1.10", "151.101.1.69", 443, 41000, 70),    # busy HTTPS download
        ("192.168.1.20", "151.101.1.69", 443, 42000, 64),    # second client, same server
        ("192.168.1.20", "140.82.112.21", 8443, 43000, 55),  # alt HTTPS port
    ]
    for client, server, port, base, n in cases:
        for k in range(n):
            eph = base + k
            # client -> server (request), then server -> client (larger reply).
            flows.append((1, GW, LOCAL_A, client, server, 6, eph, port, 200 + (k % 5) * 80))
            flows.append((1, LOCAL_A, GW, server, client, 6, port, eph, 600 + (k % 7) * 120))
    return flows


def bulk_flows():
    """~50 extra endpoints with dummy connections to a handful of hub servers, for
    a denser graph. Seeded, so output stays deterministic across runs."""
    rng = random.Random(1700)
    # Hub servers the synthetic clients talk to: (ip, server_port, ip_proto).
    # Public IPs classify as external (orange); RFC1918 as local (blue).
    hubs = [
        ("104.16.132.50", 443, 6),   # external web (TLS)
        ("104.16.132.50", 80, 6),    # external web (HTTP)
        ("8.8.8.8", 53, 17),         # external DNS
        ("10.0.0.5", 443, 6),        # internal app server
        ("10.0.0.6", 3306, 6),       # internal database
        ("192.168.1.1", 53, 17),     # gateway DNS
    ]
    # 50 fresh client endpoints across a few subnets (mix of local and external).
    clients = (
        [f"192.168.1.{100 + i}" for i in range(30)]    # local LAN
        + [f"10.0.1.{1 + i}" for i in range(10)]       # local server VLAN
        + [f"172.105.10.{20 + i}" for i in range(10)]  # external (public hosting)
    )
    flows = []
    for ci, client in enumerate(clients):
        for hub_ip, port, proto in rng.sample(hubs, k=rng.randint(2, 3)):
            for k in range(rng.randint(1, 4)):  # a few ephemeral ports per conversation
                eph = 45000 + ci * 20 + (port % 17) + k
                cnt = rng.randint(2, 6)
                flows.append((cnt, UNI, LOCAL_A, client, hub_ip, proto, eph, port,
                              rng.choice([120, 240, 480])))
                flows.append((cnt, LOCAL_A, UNI, hub_ip, client, proto, port, eph,
                              rng.choice([300, 700, 1400])))
    return flows


FLOWS = CURATED + fanout_flows() + bulk_flows()


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/captures/sample.pcap")
    out.parent.mkdir(parents=True, exist_ok=True)

    ts = 1_700_000_000
    records = []
    for count, ed, es, s, d, p, sp, dp, pl in FLOWS:
        data = frame(ed, es, s, d, p, sp, dp, pl)
        for _ in range(count):
            records.append((ts, data))
            ts += 1

    with open(out, "wb") as f:
        # classic pcap global header (big-endian), Ethernet link type
        f.write(struct.pack("!IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1))
        for t, data in records:
            f.write(struct.pack("!IIII", t, 0, len(data), len(data)))
            f.write(data)

    print(f"wrote {len(records)} packets to {out}")


if __name__ == "__main__":
    main()
