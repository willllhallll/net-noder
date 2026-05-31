"""Queries for the four UI views, over the single read-only DuckDB store.

The store holds the analytical tables plus `names` (given-names, loaded from
names.csv by ingest) and `layer_colours` (the persisted tier+colour registry). The
API only reads. Peer/dissector model: no roles, no IANA services, no ephemeral
filtering, no local/remote. Canonical pairs use the same lexical `ip_a <= ip_b`
ordering the ingest applied, so Python's `sorted([a, b])` reproduces it.
"""

MAX_GRAPH_NODES = 10000

_CONN_COLS = (
    "connection_id, ip_a, ip_b, pkts_a2b, bytes_a2b, pkts_b2a, bytes_b2a, "
    "first_seen, last_seen"
)
# Node projection + the given-name join (names live in the same store now).
_NODE_SELECT = (
    "e.ip, e.kind, n.given_name, e.total_pkts, e.total_bytes, e.degree, "
    "e.first_seen, e.last_seen"
)
_NODE_FROM = "endpoints e LEFT JOIN names n ON n.ip = e.ip"


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(int(v), hi))


def _node_dict(r) -> dict:
    return {
        "ip": r[0], "kind": r[1], "given_name": r[2], "total_pkts": r[3],
        "total_bytes": r[4], "degree": r[5], "first_seen": r[6], "last_seen": r[7],
    }


def _fetch_nodes(cur, ips) -> list[dict]:
    ips = list(ips)
    if not ips:
        return []
    ph = ",".join("?" * len(ips))
    rows = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} WHERE e.ip IN ({ph})", ips
    ).fetchall()
    return [_node_dict(r) for r in rows]


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


def _assemble(cur, conn_rows) -> dict:
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
    return {"nodes": _fetch_nodes(cur, ips), "edges": edges}


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

    Count = distinct connections carrying the layer. Tier, colour, and the
    unresolved flag come from `layer_colours` (seeded at ingest, first-seen-wins).
    """
    rows = cur.execute(
        """
        WITH lc AS (
            SELECT layer, count(DISTINCT connection_id) AS cnt
            FROM connection_layers GROUP BY layer
        )
        SELECT lc.layer, lc.cnt, c.tier, c.colour, c.unresolved
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
        }
        for layer, cnt, tier, colour, unresolved in rows
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

    g = _assemble(cur, rows)
    g["meta"] = {
        "capped": total > cap, "cap": cap,
        "shown_endpoints": len(g["nodes"]), "total_endpoints": total,
    }
    return g


def neighbors(cur, ip: str, limit: int = 50) -> dict:
    rows = cur.execute(
        f"SELECT {_CONN_COLS} FROM connections WHERE ip_a=? OR ip_b=? "
        f"ORDER BY (bytes_a2b + bytes_b2a) DESC LIMIT ?",
        [ip, ip, _clamp(limit, 1, 2000)],
    ).fetchall()
    g = _assemble(cur, rows)
    if not any(n["ip"] == ip for n in g["nodes"]):
        g["nodes"].extend(_fetch_nodes(cur, [ip]))
    return g


def node_detail(cur, ip: str):
    r = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} WHERE e.ip=?", [ip]
    ).fetchone()
    return _node_dict(r) if r else None


def connection_flows(cur, a: str, b: str):
    """The edge-click view: the pair summary + EVERY flow (canonical 5-tuple) across it.

    Each flow carries its full dissected stack (`flow_layers`, ordered from eth up).
    No cap -- every flow is returned so the UI can fan each one out as its own arrow.
    """
    ip_a, ip_b = sorted([a, b])
    c = cur.execute(
        """
        SELECT c.connection_id, c.ip_a, c.ip_b, ea.kind, eb.kind,
               na.given_name, nb.given_name,
               c.pkts_a2b, c.bytes_a2b, c.pkts_b2a, c.bytes_b2a
        FROM connections c
        JOIN endpoints ea ON ea.ip = c.ip_a
        JOIN endpoints eb ON eb.ip = c.ip_b
        LEFT JOIN names na ON na.ip = c.ip_a
        LEFT JOIN names nb ON nb.ip = c.ip_b
        WHERE c.ip_a=? AND c.ip_b=?
        """,
        [ip_a, ip_b],
    ).fetchone()
    if not c:
        return None
    flows = cur.execute(
        """
        SELECT f.flow_id, f.l4_proto, f.port_a, f.port_b,
               f.pkts_a2b, f.bytes_a2b, f.pkts_b2a, f.bytes_b2a,
               f.first_seen, f.last_seen,
               list(fl.layer ORDER BY fl.layer_index) AS layers
        FROM flows f LEFT JOIN flow_layers fl ON fl.flow_id = f.flow_id
        WHERE f.connection_id=?
        GROUP BY f.flow_id, f.l4_proto, f.port_a, f.port_b,
                 f.pkts_a2b, f.bytes_a2b, f.pkts_b2a, f.bytes_b2a,
                 f.first_seen, f.last_seen
        ORDER BY (f.bytes_a2b + f.bytes_b2a) DESC, f.flow_id
        """,
        [c[0]],
    ).fetchall()
    return {
        "connection_id": c[0], "ip_a": c[1], "ip_b": c[2],
        "kind_a": c[3], "kind_b": c[4], "name_a": c[5], "name_b": c[6],
        "pkts_a2b": c[7], "bytes_a2b": c[8], "pkts_b2a": c[9], "bytes_b2a": c[10],
        "flow_count": len(flows),
        "flows": [
            {"flow_id": f[0], "l4_proto": f[1], "port_a": f[2], "port_b": f[3],
             "pkts_a2b": f[4], "bytes_a2b": f[5], "pkts_b2a": f[6], "bytes_b2a": f[7],
             "first_seen": f[8], "last_seen": f[9], "layers": list(f[10] or [])}
            for f in flows
        ],
    }


def search(cur, q: str, limit: int = 20) -> list[dict]:
    esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = cur.execute(
        f"SELECT {_NODE_SELECT} FROM {_NODE_FROM} "
        "WHERE e.ip LIKE ? ESCAPE '\\' OR n.given_name ILIKE ? ESCAPE '\\' "
        "ORDER BY e.total_bytes DESC LIMIT ?",
        [esc + "%", f"%{esc}%", _clamp(limit, 1, 100)],
    ).fetchall()
    return [_node_dict(r) for r in rows]
