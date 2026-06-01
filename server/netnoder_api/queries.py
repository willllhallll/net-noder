"""Queries for the four UI views, over the single read-only DuckDB store.

The store holds the analytical tables plus `names` (given-names, loaded from
names.csv by ingest) and `layer_colours` (the persisted tier+colour registry). The
API only reads. Peer/dissector model: no roles, no IANA services, no ephemeral
filtering, no local/remote. Canonical pairs use the same lexical `ip_a <= ip_b`
ordering the ingest applied, so Python's `sorted([a, b])` reproduces it.
"""

from .classify import VlanClassifier, VlanDef

MAX_GRAPH_NODES = 10000

# Display order of the fixed (non-VLAN) categories, after the VLANs (ordered by seq).
_FIXED_CATEGORY_ORDER = ("public", "unassigned", "multicast", "broadcast")
_DEFAULT_COLOUR = "#60a5fa"

_CONN_COLS = (
    "connection_id, ip_a, ip_b, pkts_a2b, bytes_a2b, pkts_b2a, bytes_b2a, "
    "first_seen, last_seen"
)
# Node projection + the given-name join and the machine-resolved RDAP-name join (both
# live in the same store now). given_name is user-curated; whois_name is from `ip_whois`.
_NODE_SELECT = (
    "e.ip, e.kind, n.given_name, w.name, e.total_pkts, e.total_bytes, e.degree, "
    "e.first_seen, e.last_seen"
)
_NODE_FROM = (
    "endpoints e LEFT JOIN names n ON n.ip = e.ip "
    "LEFT JOIN ip_whois w ON w.ip = e.ip"
)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(int(v), hi))


def _load_classifier(cur) -> tuple[VlanClassifier, dict]:
    """Build the VLAN classifier + the category->colour map from the store (once/request)."""
    vlan_rows = cur.execute(
        "SELECT vlan_id, base_ip, subnet_mask, label FROM vlans"
    ).fetchall()
    vlans = [
        VlanDef(vid, base, mask, label if label else f"VLAN {vid}")
        for vid, base, mask, label in vlan_rows
    ]
    colours = {
        key: colour
        for key, colour in cur.execute(
            "SELECT category_key, colour FROM vlan_colours"
        ).fetchall()
    }
    return VlanClassifier(vlans), colours


def _node_dict(r, classifier=None, colours=None) -> dict:
    d = {
        "ip": r[0], "kind": r[1], "given_name": r[2], "whois_name": r[3],
        "total_pkts": r[4], "total_bytes": r[5], "degree": r[6],
        "first_seen": r[7], "last_seen": r[8],
    }
    if classifier is not None:
        key, label, vlan_id = classifier.classify(r[0])
        d["category"] = key
        d["category_label"] = label
        d["vlan_id"] = vlan_id
        # A directed broadcast lands in the 'broadcast' category by VLAN CIDR; reflect that
        # in `kind` too (the ingest-time IP-only kind only knows 255.255.255.255).
        if key == "broadcast":
            d["kind"] = "broadcast"
        d["colour"] = (colours or {}).get(key, _DEFAULT_COLOUR)
    return d


def _fetch_nodes(cur, ips, classifier=None, colours=None) -> list[dict]:
    ips = list(ips)
    if not ips:
        return []
    ph = ",".join("?" * len(ips))
    rows = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} WHERE e.ip IN ({ph})", ips
    ).fetchall()
    return [_node_dict(r, classifier, colours) for r in rows]


def _edge_layers(cur, ids) -> dict:
    """connection_id -> full distinct layer-token list (for client filter + colour)."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = cur.execute(
        f"SELECT connection_id, list(layer) FROM connection_layers "
        f"WHERE connection_id IN ({ph}) GROUP BY connection_id",
        ids,
    ).fetchall()
    return {cid: layers for cid, layers in rows}


def _edge_flow_counts(cur, ids) -> dict:
    """connection_id -> number of flows (distinct 5-tuples) on the edge."""
    if not ids:
        return {}
    ph = ",".join("?" * len(ids))
    rows = cur.execute(
        f"SELECT connection_id, count(*) FROM flows "
        f"WHERE connection_id IN ({ph}) GROUP BY connection_id",
        ids,
    ).fetchall()
    return {cid: n for cid, n in rows}


def _assemble(cur, conn_rows, classifier=None, colours=None) -> dict:
    ids = [r[0] for r in conn_rows]
    layer_map = _edge_layers(cur, ids)
    flow_counts = _edge_flow_counts(cur, ids)
    ips: set = set()
    edges = []
    for r in conn_rows:
        cid, ip_a, ip_b = r[0], r[1], r[2]
        ips.add(ip_a)
        ips.add(ip_b)
        edges.append({
            "connection_id": cid, "ip_a": ip_a, "ip_b": ip_b,
            "pkts": r[3] + r[5], "bytes": r[4] + r[6],
            "pkts_a2b": r[3], "bytes_a2b": r[4],
            "pkts_b2a": r[5], "bytes_b2a": r[6],
            "layers": layer_map.get(cid, []),
            "flow_count": flow_counts.get(cid, 0),
            "first_seen": r[7], "last_seen": r[8],
        })
    return {"nodes": _fetch_nodes(cur, ips, classifier, colours), "edges": edges}


def stats(cur) -> dict:
    row = cur.execute(
        """
        SELECT
          (SELECT count(*) FROM endpoints),
          (SELECT count(*) FROM connections),
          (SELECT count(*) FROM flows),
          (SELECT count(DISTINCT layer) FROM flow_layers),
          (SELECT coalesce(sum(pkts_a2b + pkts_b2a), 0) FROM connections),
          (SELECT coalesce(sum(bytes_a2b + bytes_b2a), 0) FROM connections),
          (SELECT min(first_seen) FROM endpoints),
          (SELECT max(last_seen) FROM endpoints)
        """
    ).fetchone()
    return {
        "endpoints": row[0], "connections": row[1], "flows": row[2],
        "layers": row[3], "total_pkts": row[4], "total_bytes": row[5],
        "first_seen": row[6], "last_seen": row[7],
    }


def layers(cur) -> list[dict]:
    """Observed layer set with tier + colour read from the persisted registry.

    Count = distinct connections carrying the layer. Tier, colour, and the unresolved /
    encrypted flags come from `layer_colours` (seeded at ingest, first-seen-wins).
    """
    rows = cur.execute(
        """
        WITH lc AS (
            SELECT layer, count(DISTINCT connection_id) AS cnt
            FROM connection_layers GROUP BY layer
        )
        SELECT lc.layer, lc.cnt, c.tier, c.colour, c.unresolved, c.encrypted
        FROM lc LEFT JOIN layer_colours c USING (layer)
        ORDER BY lc.cnt DESC, lc.layer
        """
    ).fetchall()
    return [
        {
            "layer": layer,
            "tier": tier or "application",
            "colour": colour or "#64748b",
            "count": cnt,
            "unresolved": bool(unresolved),
            "encrypted": bool(encrypted),
        }
        for layer, cnt, tier, colour, unresolved, encrypted in rows
    ]


def full_graph(cur, cap: int = MAX_GRAPH_NODES) -> dict:
    """Whole peer graph, bounded by a node cap (top-N endpoints by bytes when over)."""
    cap = _clamp(cap, 1, MAX_GRAPH_NODES)
    total = cur.execute("SELECT count(*) FROM endpoints").fetchone()[0]

    if total <= cap:
        rows = cur.execute(f"SELECT {_CONN_COLS} FROM connections").fetchall()
    else:
        top_ips = [
            r[0] for r in cur.execute(
                "SELECT ip FROM endpoints ORDER BY total_bytes DESC LIMIT ?", [cap]
            ).fetchall()
        ]
        ph = ",".join("?" * len(top_ips))
        rows = cur.execute(
            f"SELECT {_CONN_COLS} FROM connections "
            f"WHERE ip_a IN ({ph}) AND ip_b IN ({ph})",
            top_ips + top_ips,
        ).fetchall()

    classifier, colours = _load_classifier(cur)
    g = _assemble(cur, rows, classifier, colours)
    g["meta"] = {
        "capped": total > cap, "cap": cap,
        "shown_endpoints": len(g["nodes"]), "total_endpoints": total,
    }
    return g


def neighbors(cur, ip: str, limit: int = MAX_GRAPH_NODES) -> dict:
    rows = cur.execute(
        f"SELECT {_CONN_COLS} FROM connections WHERE ip_a=? OR ip_b=? "
        f"ORDER BY (bytes_a2b + bytes_b2a) DESC LIMIT ?",
        [ip, ip, _clamp(limit, 1, MAX_GRAPH_NODES)],
    ).fetchall()
    classifier, colours = _load_classifier(cur)
    g = _assemble(cur, rows, classifier, colours)
    if not any(n["ip"] == ip for n in g["nodes"]):
        g["nodes"].extend(_fetch_nodes(cur, [ip], classifier, colours))
    return g


def node_detail(cur, ip: str):
    r = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} WHERE e.ip=?", [ip]
    ).fetchone()
    if not r:
        return None
    classifier, colours = _load_classifier(cur)
    return _node_dict(r, classifier, colours)


def connection_stacks(cur, a: str, b: str):
    """The edge-click view: the pair summary + its flows GROUPED by de-noised stack.

    Each flow's stack is the PRESENCE set in `flow_layers` (ordered from eth up), cut at the
    ENCRYPTION boundary: tokens positioned AFTER an `encrypted`-flagged layer (tls/ssl/dtls,
    from `layer_colours.encrypted` -- never a literal protocol name) are dropped *when that
    session's version is known*, so a TLS 1.2 certificate's ASN.1 cascade never appears and
    never perturbs identity (the cut is purely positional -- adding/removing a cert element
    leaves the key byte-for-byte unchanged). Cleartext payload-content (e.g. http>media) is
    NOT a sealed boundary, so it is kept -- the full, honest picture. Flows sharing that cut
    stack collapse into one row, BUT the grouping is also keyed by `l4_proto` and
    `protocol_version`, so a transport/protocol style is NEVER merged with another:
    tcp-tls·1.2, udp-quic-tls·1.3 (HTTP/3), http·1.1, and bare tcp stay distinct groups.
    No cap: port churn lives inside `ports_a`/`ports_b`, not in a fan of thousands of edges.
    """
    ip_a, ip_b = sorted([a, b])
    c = cur.execute(
        """
        SELECT c.connection_id, c.ip_a, c.ip_b, ea.kind, eb.kind,
               na.given_name, nb.given_name, wa.name, wb.name,
               c.pkts_a2b, c.bytes_a2b, c.pkts_b2a, c.bytes_b2a
        FROM connections c
        JOIN endpoints ea ON ea.ip = c.ip_a
        JOIN endpoints eb ON eb.ip = c.ip_b
        LEFT JOIN names na ON na.ip = c.ip_a
        LEFT JOIN names nb ON nb.ip = c.ip_b
        LEFT JOIN ip_whois wa ON wa.ip = c.ip_a
        LEFT JOIN ip_whois wb ON wb.ip = c.ip_b
        WHERE c.ip_a=? AND c.ip_b=?
        """,
        [ip_a, ip_b],
    ).fetchone()
    if not c:
        return None
    # Classify both endpoints so the flow view matches the base graph: the same category
    # colour, and kind='broadcast' for a directed broadcast (which the DB stores 'unicast').
    classifier, colours = _load_classifier(cur)
    key_a = classifier.classify(c[1])[0]
    key_b = classifier.classify(c[2])[0]
    colour_a = colours.get(key_a, _DEFAULT_COLOUR)
    colour_b = colours.get(key_b, _DEFAULT_COLOUR)
    kind_a = "broadcast" if key_a == "broadcast" else c[3]
    kind_b = "broadcast" if key_b == "broadcast" else c[4]
    stacks = cur.execute(
        """
        WITH enc_boundary AS (
            -- first position of an encryption-boundary layer per flow (NULL if none).
            -- Driven by the registry's `encrypted` flag, so it covers tls/ssl/dtls without
            -- naming any of them here.
            SELECT fl.flow_id, min(fl.layer_index) AS enc_pos
            FROM flow_layers fl
            JOIN layer_colours lc ON lc.layer = fl.layer AND lc.encrypted
            GROUP BY fl.flow_id
        ),
        per_flow AS (
            SELECT f.flow_id, f.l4_proto, f.protocol_version, f.port_a, f.port_b,
                   f.pkts_a2b, f.bytes_a2b, f.pkts_b2a, f.bytes_b2a,
                   f.first_seen, f.last_seen,
                   string_agg(fl.layer, '>' ORDER BY fl.layer_index) FILTER (
                       WHERE fl.layer IS NOT NULL
                         AND (eb.enc_pos IS NULL OR f.protocol_version IS NULL
                              OR fl.layer_index <= eb.enc_pos)
                   ) AS stack_key,
                   list(fl.layer ORDER BY fl.layer_index) FILTER (
                       WHERE fl.layer IS NOT NULL
                         AND (eb.enc_pos IS NULL OR f.protocol_version IS NULL
                              OR fl.layer_index <= eb.enc_pos)
                   ) AS layers
            FROM flows f
            LEFT JOIN flow_layers fl ON fl.flow_id = f.flow_id
            LEFT JOIN enc_boundary eb ON eb.flow_id = f.flow_id
            WHERE f.connection_id=?
            GROUP BY f.flow_id, f.l4_proto, f.protocol_version, f.port_a, f.port_b,
                     f.pkts_a2b, f.bytes_a2b, f.pkts_b2a, f.bytes_b2a,
                     f.first_seen, f.last_seen
        )
        SELECT any_value(layers) AS layers, l4_proto, protocol_version,
               count(*) AS flow_count,
               sum(pkts_a2b) AS pkts_a2b, sum(bytes_a2b) AS bytes_a2b,
               sum(pkts_b2a) AS pkts_b2a, sum(bytes_b2a) AS bytes_b2a,
               min(first_seen) AS first_seen, max(last_seen) AS last_seen,
               list(DISTINCT port_a) FILTER (port_a IS NOT NULL) AS ports_a,
               list(DISTINCT port_b) FILTER (port_b IS NOT NULL) AS ports_b
        FROM per_flow
        GROUP BY stack_key, l4_proto, protocol_version
        ORDER BY sum(bytes_a2b + bytes_b2a) DESC, stack_key
        """,
        [c[0]],
    ).fetchall()
    total_flows = sum(s[3] for s in stacks)
    return {
        "connection_id": c[0], "ip_a": c[1], "ip_b": c[2],
        "kind_a": kind_a, "kind_b": kind_b, "name_a": c[5], "name_b": c[6],
        "whois_name_a": c[7], "whois_name_b": c[8],
        "colour_a": colour_a, "colour_b": colour_b,
        "pkts_a2b": c[9], "bytes_a2b": c[10], "pkts_b2a": c[11], "bytes_b2a": c[12],
        "flow_count": total_flows,
        "stacks": [
            {"layers": [x for x in (s[0] or []) if x is not None],
             "l4_proto": s[1], "protocol_version": s[2],
             "flow_count": s[3],
             "pkts_a2b": s[4], "bytes_a2b": s[5], "pkts_b2a": s[6], "bytes_b2a": s[7],
             "first_seen": s[8], "last_seen": s[9],
             "ports_a": sorted(s[10] or []), "ports_b": sorted(s[11] or [])}
            for s in stacks
        ],
    }


def search(cur, q: str, limit: int = 20) -> list[dict]:
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} "
        "WHERE e.ip LIKE ? ESCAPE '\\' OR n.given_name ILIKE ? ESCAPE '\\' "
        "OR w.name ILIKE ? ESCAPE '\\' "
        "ORDER BY e.total_bytes DESC LIMIT ?",
        [esc + "%", f"%{esc}%", f"%{esc}%", _clamp(limit, 1, 100)],
    ).fetchall()
    classifier, colours = _load_classifier(cur)
    return [_node_dict(r, classifier, colours) for r in rows]


def categories(cur) -> list[dict]:
    """The full category->colour registry for the filter legend.

    Returns every row in `vlan_colours` (VLANs first, ordered by their allocation seq,
    then the fixed buckets in a stable display order) so the filter is complete even
    for categories that have no visible endpoints in the current view."""
    rows = cur.execute(
        "SELECT category_key, label, colour, seq FROM vlan_colours"
    ).fetchall()
    fixed_rank = {k: i for i, k in enumerate(_FIXED_CATEGORY_ORDER)}

    def sort_key(r):
        key, _label, _colour, seq = r
        if seq is not None and seq >= 0:  # a VLAN: sort first by allocation order
            return (0, seq, key)
        return (1, fixed_rank.get(key, 99), key)  # fixed buckets after, in fixed order

    return [
        {"category_key": key, "label": label, "colour": colour}
        for key, label, colour, _seq in sorted(rows, key=sort_key)
    ]
