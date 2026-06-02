import { useEffect, useRef } from "react";
import cytoscape, { Core, ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { LabelOpts } from "../types";
import { resolveLabel } from "../format";
import { afterPaint, beginLoad, endLoad } from "../loading";

cytoscape.use(fcose);

// Vertical offset (model units) between adjacent parallel flow edges. Reused both as
// the bezier fan spacing and to size the endpoint gap so the lens stays compact.
const FLOW_STEP = 32;

const STYLE: any[] = [
  {
    selector: "node",
    style: {
      "background-color": "data(color)",
      width: "data(size)",
      height: "data(size)",
      label: "data(label)",
      "font-size": 7,
      color: "#cbd5e1",
      "text-valign": "center",
      "text-halign": "center",
      "text-outline-color": "#0b0f17",
      "text-outline-width": 1.4,
      "border-width": 1,
      "border-color": "#0b0f17",
      "min-zoomed-font-size": 8,
    },
  },
  {
    selector: "node:selected",
    style: { "border-width": 3, "border-color": "#f1fa8c" },
  },
  {
    selector: "edge",
    style: {
      width: "data(weight)",
      "line-color": "data(color)",
      // Dashed = an unresolved representation (dissection stopped at 'data').
      "line-style": "data(lineStyle)",
      "line-dash-pattern": [6, 3],
      "curve-style": "bezier",
      // Fan-out for the flow view: parallel edges between the same two endpoints
      // spread enough that each flow (and its label) has room and they don't overlap.
      "control-point-step-size": FLOW_STEP,
      // Per-edge opacity drives the tier filter: edges with no protocol at the
      // active tier are nearly transparent ("dimmed out").
      opacity: "data(opacity)",
      // Two-line label (protocol over ×count), wrapped on the embedded newline and
      // centred together mid-edge.
      label: "data(label)",
      "font-size": 6,
      "line-height": 1.15,
      color: "#cbd5e1",
      "text-wrap": "wrap",
      "text-rotation": "none",
      "text-events": "yes",
      "text-background-color": "#0b0f17",
      "text-background-opacity": 0.82,
      "text-background-padding": 2,
      "text-background-shape": "roundrectangle",
      "text-border-color": "data(color)",
      "text-border-opacity": 0.7,
      "text-border-width": 1,
      // Undirected: edges never carry an arrowhead in the peer model.
      "target-arrow-shape": "none",
      "min-zoomed-font-size": 6,
    },
  },
  {
    selector: "edge:selected",
    style: {
      opacity: 1,
      "line-color": "#f1fa8c",
      "text-background-opacity": 0.95,
      "z-index": 10,
    },
  },
];

// The right-side drawer overlays the canvas; leave a margin past it (matching the
// drawer's max width in styles.css) so the flow view's content is never centred under it.
const DRAWER_W = 380;

function layoutFor(nodeCount: number): any {
  const big = nodeCount > 1500;
  return {
    name: "fcose",
    quality: big ? "draft" : "default",
    animate: !big,
    animationDuration: 500,
    // Seed from random positions: freshly-added nodes all sit at the origin, and
    // fcose can't separate coincident nodes from a cold start (they collapse to a
    // diagonal line). Randomising guarantees the spread-out layout on first paint.
    randomize: true,
    fit: true,
    padding: 40,
    nodeRepulsion: 9000,
    idealEdgeLength: 90,
  };
}

// Flow-fan view: place the two endpoints at a COMPACT, controlled horizontal gap
// (proportional to the fan height) rather than spread across the viewport. Keeping
// the content small in model space lets fitFlowView zoom right in, so the endpoint
// and flow labels are large and readable by default.
function placeFlowNodes(cy: Core): void {
  const nodes = cy.nodes();
  if (nodes.length !== 2) return;
  const lensHalf = Math.max(
    FLOW_STEP,
    ((cy.edges().length - 1) * FLOW_STEP) / 2,
  );
  const gap = Math.max(220, lensHalf * 1.3); // a bit narrower than the fan is tall
  nodes[0].position({ x: -gap / 2, y: 0 }); // ip_a (left)
  nodes[1].position({ x: gap / 2, y: 0 }); // ip_b (right)
}

// Fit the flow-fan into the visible region to the LEFT of the drawer, so the
// right-most endpoint sits beside the drawer rather than hidden behind it, zoomed in
// enough (cap 3.5x) that labels are legible.
function fitFlowView(cy: Core): void {
  const W = cy.width();
  const H = cy.height();
  const avail = Math.max(240, W - DRAWER_W);
  const pad = 50;
  const bb = cy.elements().boundingBox();
  if (bb.w === 0 || bb.h === 0) return;
  const zoom = Math.max(
    0.05,
    Math.min((avail - 2 * pad) / bb.w, (H - 2 * pad) / bb.h, 3.5),
  );
  cy.zoom(zoom);
  // Centre the content within the available (left-of-drawer) region.
  cy.pan({
    x: avail / 2 - zoom * (bb.x1 + bb.w / 2),
    y: H / 2 - zoom * (bb.y1 + bb.h / 2),
  });
}

interface Props {
  elements: ElementDefinition[];
  labelOpts: LabelOpts;
  onNodeTap: (ip: string) => void;
  onEdgeTap: (data: any) => void;
  onBackgroundTap?: () => void;
  // Reports whether the graph is mid-relayout, so the parent can veil the canvas. The
  // expensive fcose layout runs after React commits, so React's own pending state can't
  // see it — we surface it from cytoscape's layout lifecycle instead.
  onBusyChange?: (busy: boolean) => void;
}

function nodeLabel(d: any, opts: LabelOpts): string {
  return resolveLabel(opts, d.name ?? null, d.whois ?? null, d.ip);
}

export default function GraphCanvas({
  elements,
  labelOpts,
  onNodeTap,
  onEdgeTap,
  onBackgroundTap,
  onBusyChange,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const cbRef = useRef({ onNodeTap, onEdgeTap, onBackgroundTap, onBusyChange });
  cbRef.current = { onNodeTap, onEdgeTap, onBackgroundTap, onBusyChange };
  // Latest label choice, read inside the (deferred) relayout so freshly-added nodes are
  // labelled correctly without re-subscribing the layout effect to labelOpts.
  const labelRef = useRef(labelOpts);
  labelRef.current = labelOpts;
  // Signature of the id-set last COMMITTED to the canvas. Comparing the incoming signature
  // against this tells us — in O(1) — whether the node/edge set changed (relayout needed)
  // or only data changed (in-place update). Starts at the empty-set signature so the first
  // render of an empty graph is a no-op, matching the initial canvas. Updated only when a
  // path actually commits, so a cancelled (superseded) update can't desync it.
  const committedSigRef = useRef("0:0");

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      style: STYLE,
      elements: [],
      wheelSensitivity: 0.2,
      hideEdgesOnViewport: true,
      textureOnViewport: true,
      motionBlur: true,
    });
    cyRef.current = cy;
    cy.on("tap", "node", (e) => cbRef.current.onNodeTap(e.target.id()));
    cy.on("tap", "edge", (e) => cbRef.current.onEdgeTap(e.target.data()));
    // Tap on empty canvas (not a node/edge) — used to de-select the focused flow.
    cy.on("tap", (e) => {
      if (e.target === cy) cbRef.current.onBackgroundTap?.();
    });
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // Sync elements into cytoscape. The node/edge SET changing means a relayout — and the
  // fcose layout runs SYNCHRONOUSLY on the main thread, freezing the page for as long as
  // it takes (seconds, for a large host graph). If we simply did the work here the freeze
  // would start before the browser could paint any "loading" feedback, so the user's click
  // would look dead until the layout finished. Instead we detect the set change cheaply,
  // raise the loading/veil signal, await a real paint (afterPaint), and only THEN do the
  // heavy diff + layout — so the indicator is on-screen (and animating on the compositor)
  // before the freeze. A pure data update (e.g. a tier recolour keeps the same ids) needs
  // no relayout and is applied in place, synchronously, with no veil.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    // Build the incoming id set and an order-independent signature of it in a SINGLE pass.
    // The signature is `size:hash`, where hash is the commutative sum of a per-id string
    // hash — so it doesn't depend on element order, and matching ids in any order produce
    // the same value. This replaces the old per-id `cy.getElementById()` membership scan
    // (an O(n) sweep of cytoscape-internal lookups on every update); comparing two short
    // signature strings is O(1) and folds its only loop into the map we already run here.
    let hash = 0;
    const incomingIds = new Set(
      elements.map((e) => {
        const id = String(e.data!.id);
        let h = 0;
        for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
        hash = (hash + h) | 0;
        return id;
      }),
    );
    const incomingSig = `${incomingIds.size}:${hash}`;

    // Set unchanged → data-only update (e.g. a tier recolour keeps the same ids): apply in
    // place, no relayout, no veil. A signature collision would only mis-route a same-size
    // id-swap into this path, which the data types and UI/UX flows don't produce in practice.
    if (incomingSig === committedSigRef.current) {
      cy.batch(() => {
        elements.forEach((el) => {
          cy.getElementById(String(el.data!.id)).data(el.data!);
        });
        // The data refresh above resets each node's label to its raw id, so re-resolve
        // node labels to the current label choice (edge labels are data-driven).
        cy.nodes().forEach((nd) => {
          nd.data("label", nodeLabel(nd.data(), labelRef.current));
        });
      });
      return;
    }

    // Relayout path: light the indicator + veil now, balanced by `release()` exactly once.
    beginLoad();
    cbRef.current.onBusyChange?.(true);
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      cbRef.current.onBusyChange?.(false);
      endLoad();
    };

    let cancelled = false;
    afterPaint().then(() => {
      // Bail if a newer elements set superseded us, or the graph was torn down, while we
      // waited for the paint — the cleanup's release() has already balanced the signal.
      if (cancelled || cyRef.current !== cy) return;

      // Heavy diff: drop removed, add new, update the rest — then relabel so freshly-added
      // nodes honour the current label choice.
      cy.batch(() => {
        cy.elements().forEach((el) => {
          if (!incomingIds.has(el.id())) el.remove();
        });
        elements.forEach((el) => {
          const existing = cy.getElementById(String(el.data!.id));
          if (existing.empty()) cy.add(el);
          else existing.data(el.data!);
        });
        cy.nodes().forEach((nd) => {
          nd.data("label", nodeLabel(nd.data(), labelRef.current));
        });
      });
      // This set is now on the canvas — record its signature so a following data-only
      // update is recognised. Done only here (post-commit), never for a cancelled update.
      committedSigRef.current = incomingSig;

      const n = cy.nodes().length;
      if (n === 2) {
        // Flow-fan view: position the pair compactly, then fit zoomed-in & clear of the
        // drawer (no force layout needed for two fixed endpoints) — synchronous.
        try {
          placeFlowNodes(cy);
          fitFlowView(cy);
        } finally {
          release();
        }
      } else {
        // Force layout is asynchronous: hold the signal until layoutstop fires.
        const layout = cy.layout(layoutFor(n));
        layout.one("layoutstop", release);
        try {
          layout.run();
        } catch {
          release();
        }
      }
    });

    return () => {
      // A new elements set arrived (or we unmounted) before this one settled — cancel the
      // pending work and balance the signal so the veil/bar can't get stuck on.
      cancelled = true;
      release();
    };
  }, [elements]);

  // Relabel existing nodes when the label choice changes on its own (no element-set change
  // — that case is handled inside the relayout above using labelRef).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      n.data("label", nodeLabel(n.data(), labelOpts));
    });
  }, [labelOpts]);

  return <div ref={containerRef} className="graph" />;
}
