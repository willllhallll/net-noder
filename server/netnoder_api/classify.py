"""Classify endpoints into colour categories by subnet membership.

There is no 802.1Q tag in the pipeline -- classification is purely IP-vs-CIDR using the
stdlib `ipaddress` module. VLAN definitions come from the `vlans` table (loaded from
vlans.csv at ingest); every endpoint falls into exactly one category:

  * VLAN N      - unicast IP inside that VLAN's subnet (excluding its directed broadcast).
  * Public      - globally-routable unicast not in any configured VLAN.
  * Unassigned  - private (RFC1918/ULA) unicast not in any configured VLAN, plus any
                  unparseable address (its own grey bucket).
  * Multicast   - 224.0.0.0/4 or IPv6 ff00::/8. UNIVERSAL: a multicast address cannot be
                  attributed to a VLAN, so it is one shared category, never per-VLAN.
  * Broadcast   - the limited broadcast 255.255.255.255 AND every VLAN's directed broadcast
                  (the subnet's all-ones host). UNIVERSAL: a broadcast address is not a host,
                  so it shares one Broadcast bucket/colour rather than sitting in its VLAN.

Category keys mirror `broadcast_domain_colours.category_key`: 'vlan_<id>' | 'public' | 'unassigned'
| 'multicast' | 'broadcast'.
"""
from dataclasses import dataclass
from ipaddress import ip_address, ip_network


@dataclass
class VlanDef:
    """One VLAN definition, with its derived network + directed-broadcast address.

    A bad base_ip/subnet_mask pair yields `network = None`, and the VLAN is then skipped
    during classification rather than raising."""
    vlan_id: int
    base_ip: str
    subnet_mask: str
    label: str

    def __post_init__(self) -> None:
        self.network = None
        self.broadcast = None
        try:
            prefix_len = bin(int(ip_address(self.subnet_mask))).count("1")
            self.network = ip_network(f"{self.base_ip}/{prefix_len}", strict=False)
            self.broadcast = self.network.broadcast_address
        except ValueError:
            pass

    @property
    def key(self) -> str:
        return f"vlan_{self.vlan_id}"


class VlanClassifier:
    """Classifies an IP string into (category_key, category_label, vlan_id|None).

    A directed broadcast (a VLAN subnet's all-ones host, e.g. 10.200.0.255) is detected
    using the VLAN's CIDR -- query-time knowledge the ingest-time `kind` lacks -- and folds
    into the UNIVERSAL Broadcast bucket alongside 255.255.255.255, not into its VLAN. The
    'broadcast' category key is therefore the API's authority on broadcast-ness; callers
    surface kind='broadcast' from it (see queries._node_dict)."""

    def __init__(self, vlans: list[VlanDef]):
        # Keep only well-formed VLANs (parseable network), in vlan_id order so the
        # first matching subnet wins deterministically.
        self.vlans = sorted(
            (v for v in vlans if v.network is not None), key=lambda v: v.vlan_id
        )

    def classify(self, ip_str: str) -> tuple[str, str, int | None]:
        try:
            ip = ip_address(ip_str)
        except ValueError:
            return ("unassigned", "Unassigned", None)

        if ip.version == 4 and ip == ip_address("255.255.255.255"):
            return ("broadcast", "Broadcast", None)
        if ip.is_multicast:
            return ("multicast", "Multicast", None)

        for v in self.vlans:
            if ip.version != v.network.version:
                continue
            # Checked before `in v.network` (the directed broadcast is itself in-subnet).
            if ip == v.broadcast:
                return ("broadcast", "Broadcast", None)
            if ip in v.network:
                return (v.key, v.label, v.vlan_id)

        if ip.is_private:
            return ("unassigned", "Unassigned", None)
        return ("public", "Public", None)
