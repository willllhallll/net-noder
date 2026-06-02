import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import type { ElementDefinition } from "cytoscape";
import { api } from "./api";
import { beginLoad, endLoad } from "./loading";
import type {
  Category,
  ConnectionStack,
  ConnectionStacks,
  Graph,
  GraphMeta,
  LabelOpts,
  Layer,
  NodeT,
  Stats,
  Tier,
} from "./types";
import {
  buildLayerLookup,
  edgeStyle,
  edgeWidth,
  fmtBytes,
  fmtNum,
  isUnresolved,
  NEUTRAL_EDGE,
  nodeColor,
  nodeSize,
  shortVersion,
  tokenAtTier,
  versionOwnerToken,
} from "./format";
import GraphCanvas from "./components/GraphCanvas";
import SearchBar from "./components/SearchBar";
import FilterPanel from "./components/FilterPanel";
import CategoryFilter from "./components/CategoryFilter";
import TierFilter from "./components/TierFilter";
import EndpointPanel from "./components/EndpointPanel";
import ConnectionPanel from "./components/ConnectionPanel";
import StackPanel from "./components/StackPanel";
import CapNoticeModal from "./components/CapNoticeModal";
import LoadingBar from "./components/LoadingBar";
import CanvasOverlay from "./components/CanvasOverlay";
import Drawer from "./components/Drawer";
import DrawerSkeleton from "./components/DrawerSkeleton";

type LK = ReturnType<typeof buildLayerLookup>;

// Base graph (hosts / focus): nodes + NEUTRAL, unlabelled edges. An edge is just
// "a connection of any type" — protocol detail is revealed only on drill-down. Nodes
// are coloured by broadcast domain. When `activeCats` is supplied (hosts view),
// nodes whose broadcast domain is toggled off are dropped, along with any edge touching them;
// when it is undefined (focus view) everything is shown.
function graphToElements(
  g: Graph,
  activeCats?: Set<string>,
): ElementDefinition[] {
  const visible = g.nodes.filter(
    (n) => !activeCats || activeCats.has(n.category),
  );
  const visibleIps = new Set(visible.map((n) => n.ip));
  const nodes: ElementDefinition[] = visible.map((n) => ({
    data: {
      id: n.ip,
      ip: n.ip,
      name: n.given_name,
      whois: n.whois_name,
      label: n.ip, // GraphCanvas corrects this per the label toggles
      size: nodeSize(n.total_bytes),
      color: nodeColor(n),
    },
  }));
  const edges: ElementDefinition[] = g.edges
    .filter((e) => visibleIps.has(e.ip_a) && visibleIps.has(e.ip_b))
    .map((e) => ({
      data: {
        id: `e${e.connection_id}`,
        source: e.ip_a,
        target: e.ip_b,
        weight: edgeWidth(e.bytes),
        color: NEUTRAL_EDGE,
        // Label the connection with how many flows the pair shares (density at a glance).
        label: `${e.flow_count}`,
        opacity: 0.5,
        lineStyle: "solid",
      },
    }));
  return [...nodes, ...edges];
}

// Node/edge counts for a base graph under the category filter — the same visibility rule
// graphToElements applies (edges survive only when both endpoints do), used for the
// filter-window header in both the hosts and focus views.
function countVisible(g: Graph, activeCats: Set<string>): { nodes: number; edges: number } {
  const visible = g.nodes.filter((n) => activeCats.has(n.category));
  const visibleIps = new Set(visible.map((n) => n.ip));
  const edges = g.edges.filter(
    (e) => visibleIps.has(e.ip_a) && visibleIps.has(e.ip_b),
  );
  return { nodes: visible.length, edges: edges.length };
}

// Flow-fan view: the two endpoints + one edge PER STACK (flows sharing an identical
// full dissected stack are collapsed). Each stack edge is coloured by the protocol it
// carries at the active tier, and labelled with that protocol plus the flow count that
// collapsed into it — so port churn becomes a handful of arrows, not thousands.
function stacksToElements(
  conn: ConnectionStacks,
  tier: Tier,
  lk: LK,
): ElementDefinition[] {
  const totalBytes = conn.bytes_a2b + conn.bytes_b2a;
  const nodes: ElementDefinition[] = [
    {
      data: {
        id: conn.ip_a,
        ip: conn.ip_a,
        name: conn.name_a,
        whois: conn.whois_name_a,
        label: conn.ip_a,
        size: nodeSize(totalBytes),
        color: nodeColor({ colour: conn.colour_a }),
      },
    },
    {
      data: {
        id: conn.ip_b,
        ip: conn.ip_b,
        name: conn.name_b,
        whois: conn.whois_name_b,
        label: conn.ip_b,
        size: nodeSize(totalBytes),
        color: nodeColor({ colour: conn.colour_b }),
      },
    },
  ];
  const edges: ElementDefinition[] = conn.stacks.map((s, i) => {
    const st = edgeStyle(s.layers, tier, lk);
    // When the visible token OWNS the flow's protocol_version, fold it into the label so it
    // reads e.g. "tls·1.3 ×210" or "http·1.1 ×42" — but never on a token the version does
    // not belong to (e.g. a TLS version under the tcp chip), where it would be misleading.
    const ver =
      st.token && st.token === versionOwnerToken(s.protocol_version)
        ? shortVersion(s.protocol_version)
        : null;
    const proto = st.label ? `${st.label}${ver ? `·${ver}` : ""}` : "";
    return {
      data: {
        id: `s${i}`,
        source: conn.ip_a,
        target: conn.ip_b,
        weight: edgeWidth(s.bytes_a2b + s.bytes_b2a),
        color: st.color,
        // Mid-edge label: the protocol at the active tier (blank when the tier has none)
        // followed by the collapsed-flow count on the same line, e.g. "dns ×10,826".
        label: proto
          ? `${proto} ×${fmtNum(s.flow_count)}`
          : `×${fmtNum(s.flow_count)}`,
        opacity: st.opacity,
        lineStyle: st.dashed ? "dashed" : "solid",
      },
    };
  });
  return [...nodes, ...edges];
}

type GraphView =
  | { kind: "hosts" }
  | { kind: "focus"; node: NodeT; graph: Graph }
  | { kind: "flows"; conn: ConnectionStacks; from: GraphView };
type DrawerState =
  | null
  // Transient state while a drawer's detail data is in flight — renders a skeleton.
  | { kind: "loading" }
  | { kind: "connection"; conn: ConnectionStacks }
  | { kind: "stack"; conn: ConnectionStacks; stack: ConnectionStack };

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [layers, setLayers] = useState<Layer[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [activeCats, setActiveCats] = useState<Set<string>>(new Set());
  const [labelOpts, setLabelOpts] = useState<LabelOpts>({ given: true, whois: false });
  const [activeTier, setActiveTier] = useState<Tier>("application");
  const [hostGraph, setHostGraph] = useState<Graph | null>(null);
  const [graphView, setGraphView] = useState<GraphView>({ kind: "hosts" });
  const [drawer, setDrawer] = useState<DrawerState>(null);
  const [capNotice, setCapNotice] = useState<GraphMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  // True while cytoscape is relaying out — reported by GraphCanvas, drives the canvas veil.
  const [graphBusy, setGraphBusy] = useState(false);
  // Filter toggles (category/tier) trigger a heavy elements rebuild + relayout but no
  // fetch; marking those state updates as a transition keeps the UI responsive and gives
  // us isPending to feed the global loading signal.
  const [isPending, startTransition] = useTransition();

  // A React transition (heavy fetch-free state change) feeds the shared loading signal so
  // the top bar reflects it. The other non-fetch lag source — the cytoscape relayout — is
  // bracketed at its own choke point inside GraphCanvas, where it can force a paint of the
  // loading state BEFORE the synchronous fcose layout freezes the main thread. (Fetches
  // feed the signal directly from api.ts; the relayout veil is driven by graphBusy below.)
  useEffect(() => {
    if (!isPending) return;
    beginLoad();
    return () => endLoad();
  }, [isPending]);

  const lk = useMemo(() => buildLayerLookup(layers), [layers]);

  const elements = useMemo(() => {
    if (graphView.kind === "flows") {
      return stacksToElements(graphView.conn, activeTier, lk);
    }
    if (graphView.kind === "focus") {
      // Focus view carries the same category filter as the hosts view.
      return graphToElements(graphView.graph, activeCats);
    }
    return hostGraph ? graphToElements(hostGraph, activeCats) : [];
  }, [graphView, hostGraph, activeTier, lk, activeCats]);

  // Tokens present at the active tier across the visible flows — the scoped legend.
  const tierLegend = useMemo(() => {
    if (graphView.kind !== "flows") return [];
    const seen = new Set<string>();
    const items: { token: string; colour: string; unresolved: boolean }[] = [];
    for (const s of graphView.conn.stacks) {
      const tok = tokenAtTier(s.layers, activeTier, lk);
      if (tok && !seen.has(tok)) {
        seen.add(tok);
        items.push({
          token: tok,
          colour: lk.map.get(tok)?.colour ?? NEUTRAL_EDGE,
          unresolved: isUnresolved(tok, lk),
        });
      }
    }
    return items.sort(
      (a, b) => (lk.rank.get(a.token) ?? 0) - (lk.rank.get(b.token) ?? 0),
    );
  }, [activeTier, graphView, lk]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [s, g, ls, cats] = await Promise.all([
          api.stats(),
          api.graph(),
          api.layers(),
          api.categories(),
        ]);
        if (cancelled) return;
        setStats(s);
        setLayers(ls);
        setCategories(cats);
        setActiveCats(new Set(cats.map((c) => c.category_key)));
        setHostGraph(g);
        setCapNotice(g.meta?.capped ? g.meta : null);
        setGraphView({ kind: "hosts" });
        setDrawer(null);
        setError(null);
      } catch (e: any) {
        if (!cancelled) setError(String(e.message ?? e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const focusNode = useCallback(async (ip: string) => {
    setDrawer({ kind: "loading" }); // skeleton until the endpoint view takes over
    try {
      const [g, node] = await Promise.all([
        api.neighbors(ip), // no explicit limit -> server's host-view cap governs
        api.node(ip),
      ]);
      setDrawer(null);
      setGraphView({ kind: "focus", node, graph: g });
      setError(null);
    } catch (e: any) {
      setDrawer(null);
      setError(String(e.message ?? e));
    }
  }, []);

  // Edge tap dispatches on the current view: in the flow fan, an edge IS a stack;
  // otherwise it is a connection, which drills into the flow fan + summary drawer.
  const onEdgeTap = useCallback(
    async (data: any) => {
      if (graphView.kind === "flows") {
        const i = Number(String(data.id).slice(1)); // "s<index>"
        const stack = graphView.conn.stacks[i];
        if (stack) setDrawer({ kind: "stack", conn: graphView.conn, stack });
        return;
      }
      setDrawer({ kind: "loading" }); // skeleton in the drawer while stacks load
      try {
        const conn = await api.connectionStacks(data.source, data.target);
        setActiveTier("application"); // default the flow view to the application tier
        setGraphView({ kind: "flows", conn, from: graphView });
        setDrawer({ kind: "connection", conn });
        setError(null);
      } catch (e: any) {
        setDrawer(null);
        setError(String(e.message ?? e));
      }
    },
    [graphView],
  );

  // Tapping empty canvas de-selects the current stack: fall back to the connection
  // summary (the stack drawer should not linger once its edge is unfocused).
  const onBackgroundTap = useCallback(() => {
    setDrawer((d) =>
      d?.kind === "stack" ? { kind: "connection", conn: d.conn } : d,
    );
  }, []);

  // Exiting a drawer back to a base view rebuilds the (potentially large) host/focus
  // graph. Close the drawer urgently and defer the heavy graphView swap into a transition
  // so the old view stays interactive while the new elements are built; the relayout that
  // follows is bracketed (and paint-fenced) inside GraphCanvas.
  const exitFocus = useCallback(() => {
    setDrawer(null);
    startTransition(() => setGraphView({ kind: "hosts" }));
  }, []);

  const exitFlows = useCallback(() => {
    setDrawer(null);
    startTransition(() =>
      setGraphView((gv) => (gv.kind === "flows" ? gv.from : { kind: "hosts" })),
    );
  }, []);

  const counts = useMemo(() => {
    if (graphView.kind === "flows") {
      return { nodes: 2, edges: graphView.conn.stacks.length };
    }
    // Hosts & focus views count only what survives the category filter.
    if (graphView.kind === "focus") return countVisible(graphView.graph, activeCats);
    if (!hostGraph) return { nodes: 0, edges: 0 };
    return countVisible(hostGraph, activeCats);
  }, [graphView, hostGraph, activeCats]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">net-noder</div>
        {stats && (
          <div className="stat-strip">
            <span>{fmtNum(stats.endpoints)} endpoints</span>
            <span>{fmtNum(stats.connections)} connections</span>
            <span>{fmtNum(stats.flows)} flows</span>
            <span>{fmtNum(stats.layers)} protocols</span>
            <span>{fmtBytes(stats.total_bytes)}</span>
          </div>
        )}
        <SearchBar onPick={focusNode} />
      </header>

      <LoadingBar />

      {error && <div className="error">{error}</div>}

      <main className="canvas-wrap">
        <GraphCanvas
          elements={elements}
          labelOpts={labelOpts}
          onNodeTap={focusNode}
          onEdgeTap={onEdgeTap}
          onBackgroundTap={onBackgroundTap}
          onBusyChange={setGraphBusy}
        />

        {/* Graph-only veil: during the initial load (no host graph yet) or any relayout. */}
        <CanvasOverlay show={graphBusy || !hostGraph} />

        {graphView.kind === "focus" && (
          <div className="view-banner">endpoint view · {graphView.node.ip}</div>
        )}
        {graphView.kind === "flows" && (
          <div className="view-banner">
            flow view · {graphView.conn.ip_a} ↔ {graphView.conn.ip_b} ·{" "}
            {fmtNum(graphView.conn.flow_count)} flows
          </div>
        )}

        {/* The bottom-left control panel, present in every view: Labels toggles on top
            (so the label choice persists across view changes) + the per-view filter
            below — the category filter in hosts/focus, the tier stepper in the flow view. */}
        <FilterPanel
          labelOpts={labelOpts}
          setLabelOpts={setLabelOpts}
          nodeCount={counts.nodes}
          edgeCount={counts.edges}
          edgeLabel={graphView.kind === "flows" ? "flows" : "connections"}
        >
          {graphView.kind === "flows" ? (
            <TierFilter
              activeTier={activeTier}
              // Recolour/relabel is a heavy, fetch-free relayout — run it as a transition.
              setActiveTier={(t) => startTransition(() => setActiveTier(t))}
              tierLegend={tierLegend}
            />
          ) : (
            categories.length > 0 && (
              <CategoryFilter
                categories={categories}
                active={activeCats}
                // Toggling a domain rebuilds elements + relayouts — run it as a transition.
                setActive={(s) => startTransition(() => setActiveCats(s))}
              />
            )
          )}
        </FilterPanel>

        {drawer?.kind === "loading" ? (
          <Drawer onBack={() => setDrawer(null)}>
            <DrawerSkeleton />
          </Drawer>
        ) : drawer?.kind === "stack" ? (
          <StackPanel
            conn={drawer.conn}
            stack={drawer.stack}
            labelOpts={labelOpts}
            activeTier={activeTier}
            lk={lk}
            onBack={() => setDrawer({ kind: "connection", conn: drawer.conn })}
          />
        ) : drawer?.kind === "connection" ? (
          <ConnectionPanel
            conn={drawer.conn}
            labelOpts={labelOpts}
            activeTier={activeTier}
            lk={lk}
            onBack={exitFlows}
          />
        ) : graphView.kind === "focus" ? (
          <EndpointPanel data={graphView.node} labelOpts={labelOpts} onBack={exitFocus} />
        ) : null}
      </main>

      <CapNoticeModal notice={capNotice} onClose={() => setCapNotice(null)} />
    </div>
  );
}
