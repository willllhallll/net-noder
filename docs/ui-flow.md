# Web app UI flow

The explorer is a single Cytoscape graph with a right-side drawer that drills in
without losing context. The flow is:

```
graph ──tap node──> focus (endpoint) ──tap edge──> connection (protocols) ──tap protocol──> ports
```

State lives in [App.tsx](../web/src/App.tsx): a `graphView` (`hosts` | `focus`) for
what the canvas shows, and a `drawer` (`connection` | `ports`) stacked on top. The
drawer's **←** steps back one level; the endpoint drawer's **✕** returns to the full
graph.

## The graph ([GraphCanvas.tsx](../web/src/components/GraphCanvas.tsx))

- **Nodes** are coloured by `kind` only — unicast (blue), multicast (purple),
  broadcast (red) — and sized by bytes (log scale). No local/remote.
- **Edges are undirected** (no arrowheads); an edge exists iff any connection does.
- **Default view:** edges are neutral grey and labelled by their most-specific
  protocol token (e.g. `tls`, `dns`). No colour clutter until you step into a tier.

## The tier stepper ([LayerFilter.tsx](../web/src/components/LayerFilter.tsx))

The headline control walks the protocol tiers
`link → network → transport → application`. At the active tier:

- each edge is **coloured + labelled** by the specific protocol it carries there
  (stepping app→transport flips an edge from `tls` brown to `tcp` blue);
- edges with **no protocol at that tier dim out** — filtering is purely layer-based;
- the **legend is scoped to the active tier**, listing only the colours currently on
  the graph, so the colour↔protocol mapping is always unambiguous.

Colours come from `/api/layers` and are stable across sessions (see
[api.md](api.md#layers-tiers-and-colour)). Edge styling is recomputed in place when
the tier changes — no relayout.

## Endpoint drawer ([EndpointPanel.tsx](../web/src/components/EndpointPanel.tsx))

Tapping a node focuses the graph on its neighbours and opens its detail: name/IP, a
`kind` chip, degree, totals, and first/last seen. Given-names are **read-only** here
— they are sourced from `names.csv` (loaded into the store by `netnoder-names`),
shown via the label toggle and search.

## Connection drawer ([ConnectionPanel.tsx](../web/src/components/ConnectionPanel.tsx))

Tapping an edge shows the pair as **A ↔ B** (no arrows, kind dots) with neutral
**A→B / B→A** volumes, then the list of protocols the pair shares (from
`connection_protocols`). Each row is coloured by the layer palette and shows the
layer, its L4 transport, neutral per-direction volumes, and a `port_count`. The
counts are **presence-based** (a layer includes everything above it), not a
100%-summing split. Tapping a protocol opens its ports.

## Protocol-ports drawer ([ProtocolPortsPanel.tsx](../web/src/components/ProtocolPortsPanel.tsx))

Header `A ↔ B · <layer>` accented with the layer colour. Every real port-pair
carrying that layer is listed, **grouped under whichever side has fewer distinct
ports** (a neutral display compaction — a **swap** toggle flips it, not a role claim).
The peer-port list is **virtualized** to handle tens of thousands of ephemeral ports,
with an in-drawer port search. Each row shows neutral **A→B / B→A** volumes. Ports
appear only here — never on the graph.

## Search ([SearchBar.tsx](../web/src/components/SearchBar.tsx))

The top bar carries the IP/given-name label toggle and an endpoint search (IP prefix
or name substring); picking a result focuses that endpoint. When a capture exceeds
the node cap, [CapNoticeModal.tsx](../web/src/components/CapNoticeModal.tsx) explains
that the top talkers are shown and the rest is still reachable via search/drill-down.
