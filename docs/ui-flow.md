# Web app UI flow

A short guide to using the net-noder explorer, and a map of how the UI is built. The
front-end is Vite + React + [Cytoscape.js](https://js.cytoscape.org); code is in
[web/src/](../web/src/).

## Getting it on screen

- **Dev:** `cd web && npm run dev`, then open <http://localhost:5173>. Vite proxies
  `/api` to the FastAPI server on `:8000`, so run `netnoder-api` alongside it.
  (In this repo you can just run the `/dev` task, which starts both.)
- **Single URL:** `cd web && npm run build`, then `netnoder-api` serves the built app
  at <http://localhost:8000>.

If the graph is empty or you see an error strip, the database probably hasn't been
built yet — run `netnoder-ingest` first (see [ingest-pipeline.md](ingest-pipeline.md)).

## The three views

The whole app is one screen with a graph canvas and a right-side drawer. You move
through **three nested views**, each one a deeper zoom:

```
   Hosts view  ──click a node──>  Endpoint view  ──click an edge──>  Connection view
   (all hosts)                    (one host's links)                 (one pair's services)
        ^                                 │                                   │
        └──────────────── ✕ ─────────────┘                                   │
                          └──────────────────────── ← ────────────────────────┘
```

### 1. Hosts view — the whole network

On load you get the **host graph**: every endpoint as a circle, every connection as a
line. This is the bytes-ranked, capped view from `GET /api/graph`.

Reading the picture:

- **Circle size** = total bytes for that endpoint (log-scaled).
- **Circle colour** = blue local host · orange external host · purple multicast ·
  red broadcast (see the on-screen **legend**, bottom-left).
- **Line thickness** = bytes on that connection; **line colour** marks a
  multicast/broadcast connection.
- **Edge labels** preview the top services on a connection, e.g. `tcp/443`, `udp/53`,
  `+4 more`. Labels fade out when you zoom out so dense graphs stay readable.

If the capture has more endpoints than the cap, a **"Too many endpoints"** modal
appears on load: only the highest-traffic hosts are drawn, but every host is still
reachable via click-through and search — nothing is lost.

### 2. Endpoint view — focus one host

**Click a node** (or pick one from search) to focus it. The graph filters to just
that endpoint and its top connections (`GET /api/node/{ip}/neighbors`), and a drawer
opens on the right showing the endpoint's details: kind, local/external, peer count
(degree), packets, bytes, and first/last seen.

Press the drawer's **✕** to return to the full hosts view.

### 3. Connection view — one pair's conversations

**Click a connection edge** to drill in (`GET /api/connection`). The graph now shows
just the two endpoints, with **one directed edge per conversation** — each a service
on that connection. An arrowhead points **client → server**; a conversation with no
service port (e.g. ICMP) is drawn undirected.

**Click a conversation edge** to load its details into the drawer:

- the service name (from the port registry) and protocol/cast badges,
- the resolved **client → server** direction,
- per-direction packet and byte counts,
- and the **reply ports** — the (top-N by bytes) ephemeral or peer ports that talked
  to this service (`GET /api/conversation/ports`), loaded lazily on click.

The drawer's **←** button steps back one layer — to the endpoint you came from, or to
the hosts view if you drilled straight in.

## Top bar controls

- **Stat strip** — total endpoints, connections, conversations, and bytes for the
  whole capture (`GET /api/stats`).
- **Labels** — toggle node labels between **IP** and **Given name**. Names come from
  the `names` table; an endpoint with no given name still shows its IP.
- **Find endpoint** — search by IP prefix (`192.168`) or by name/hostname substring
  (`laptop`). Picking a result jumps straight to that endpoint's Endpoint view, so
  search reaches hosts that the capped host graph didn't draw.

## How the front-end is wired

| File | Role |
| ---- | ---- |
| [App.tsx](../web/src/App.tsx) | Owns the `view` state machine (`hosts` / `focus` / `connection`), fetches data, and converts API responses into Cytoscape elements. |
| [api.ts](../web/src/api.ts) | Tiny typed `fetch` wrapper, one method per endpoint. |
| [types.ts](../web/src/types.ts) | TypeScript mirrors of the API's Pydantic models. |
| [components/GraphCanvas.tsx](../web/src/components/GraphCanvas.tsx) | The Cytoscape canvas: styling, the `fcose` layout, level-of-detail/perf tuning, and node/edge tap callbacks. Updates elements incrementally. |
| [components/Controls.tsx](../web/src/components/Controls.tsx) | Labels toggle + endpoint search box. |
| [components/EndpointPanel.tsx](../web/src/components/EndpointPanel.tsx) | Endpoint-view drawer. |
| [components/ConnectionPanel.tsx](../web/src/components/ConnectionPanel.tsx) | Connection-view drawer + conversation detail + reply ports. |
| [components/Drawer.tsx](../web/src/components/Drawer.tsx) | Shared right-drawer chrome with the single back/close button. |
| [components/CapNoticeModal.tsx](../web/src/components/CapNoticeModal.tsx) | The "too many endpoints" notice. |
| [format.ts](../web/src/format.ts) | Pure helpers: byte/number/time formatting, the colour scheme, log-scaled node sizes and edge widths, and conversation direction/label resolution. |

The key idea in `App.tsx`: the view is a small discriminated union, and `elements`
(the Cytoscape graph) is *derived* from it with `useMemo`. Drilling down stores where
you came from (`origin`) so the back button can always return one layer up, and the
host graph's node elements are reused in the connection view so colours, sizes, and
labels stay consistent across views.
