#!/usr/bin/env python3
"""Write a tiny deterministic classic-pcap file for testing the ingest pipeline.

Crafts Ethernet/IPv4 frames covering unicast (TCP+UDP), multicast (mDNS), and
broadcast (DHCP) so cast-type classification and service rollups can be asserted.

Usage: python tools/make_sample_pcap.py [out.pcap]   (default: data/captures/sample.pcap)
"""
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

# count, eth_dst, eth_src, src_ip, dst_ip, proto, sport, dport, payload_len
FLOWS = [
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
    # A single host pair speaking many services, to exercise multi-port edges.
    (6, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 6, 50001, 443, 1200),  # HTTPS
    (5, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 6, 50002, 80, 800),    # HTTP
    (3, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 6, 50003, 22, 200),    # SSH
    (4, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 17, 50004, 53, 90),    # DNS
    (2, GW, LOCAL_A, "192.168.1.10", "10.0.0.5", 17, 50005, 123, 76),   # NTP
]


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
