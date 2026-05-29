"""FastAPI app: graph queries over the aggregated store, plus static web hosting.

In production, if ``web/dist`` exists it's mounted at ``/`` so the whole tool is one
local URL. In dev, run Vite separately and let it proxy ``/api`` to this server.
"""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import db, queries
from .models import (
    ConversationDetail,
    EphemeralPorts,
    Graph,
    Node,
    NodeDetail,
    Stats,
)

app = FastAPI(title="net-noder API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_con = None


def _cursor():
    """Lazily open the read-only DB; one shared connection, per-request cursor."""
    global _con
    if _con is None:
        try:
            _con = db.get_con()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=503,
                detail=f"Database unavailable at {db.DB_PATH}. Run ingest first. ({e})",
            )
    return _con.cursor()


@app.get("/api/health")
def health():
    return {"ok": True, "db": str(db.DB_PATH), "exists": db.DB_PATH.exists()}


@app.get("/api/stats", response_model=Stats)
def get_stats():
    return queries.stats(_cursor())


@app.get("/api/graph/top", response_model=Graph)
def get_top(metric: str = Query("bytes"), limit: int = Query(100, ge=1, le=2000)):
    return queries.top_graph(_cursor(), metric, limit)


@app.get("/api/node/{ip}", response_model=NodeDetail)
def get_node(ip: str):
    d = queries.node_detail(_cursor(), ip)
    if d is None:
        raise HTTPException(404, detail=f"No endpoint {ip}")
    return d


@app.get("/api/node/{ip}/neighbors", response_model=Graph)
def get_neighbors(
    ip: str, metric: str = Query("bytes"), limit: int = Query(50, ge=1, le=2000)
):
    return queries.neighbors(_cursor(), ip, metric, limit)


@app.get("/api/conversation", response_model=ConversationDetail)
def get_conversation(a: str, b: str):
    d = queries.conversation(_cursor(), a, b)
    if d is None:
        raise HTTPException(404, detail=f"No conversation {a} <-> {b}")
    return d


@app.get("/api/conversation/ports", response_model=EphemeralPorts)
def get_conversation_ports(
    a: str,
    b: str,
    proto: str,
    server_port: int,
    cast: str,
    limit: int = Query(50, ge=1, le=500),
):
    d = queries.ephemeral_ports(_cursor(), a, b, proto, server_port, cast, limit)
    if d is None:
        raise HTTPException(404, detail="No such service line")
    return d


@app.get("/api/search", response_model=list[Node])
def get_search(q: str, limit: int = Query(20, ge=1, le=100)):
    return queries.search(_cursor(), q, limit)


# Mount the built web app last so /api/* routes take precedence.
_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")


def run():
    import uvicorn

    host = os.environ.get("NETNODER_HOST", "127.0.0.1")
    port = int(os.environ.get("NETNODER_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
