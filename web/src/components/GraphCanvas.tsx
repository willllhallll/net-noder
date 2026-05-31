import { useEffect, useRef } from "react";
import cytoscape, { Core, ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { LabelMode } from "../types";

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

// The right-side drawer (320px) overlays the canvas; leave a margin past it so the
// flow view's content is never centred underneath it.
const DRAWER_W = 360;

function layoutFor(nodeCount: number): any {
  const big = nodeCount > 1500;
  return {
    name: "fcose",
    quality: big ? "draft" : "default",
    animate: !big,
    animationDuration: 500,
    randomize: false,
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
  const lensHalf = Math.max(FLOW_STEP, ((cy.edges().length - 1) * FLOW_STEP) / 2);
  const gap = Math.max(220, lensHalf * 1.3); // a bit narrower than the fan is tall
  nodes[0].position({ x: -gap / 2, y: 0 }); // ip_a (left)
  nodes[1].position({ x: gap / 2, y: 0 });  // ip_b (right)
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
    Math.min((avail - 2 * pad) / bb.w, (H - 2 * pad) / bb.h, 3.5)
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
  labelMode: LabelMode;
  onNodeTap: (ip: string) => void;
  onEdgeTap: (data: any) => void;
  onBackgroundTap?: () => void;
}

function nodeLabel(d: any, mode: LabelMode): string {
  return mode === "name" && d.name ? d.name : d.ip;
}

export default function GraphCanvas({
  elements,
  labelMode,
  onNodeTap,
  onEdgeTap,
  onBackgroundTap,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const cbRef = useRef({ onNodeTap, onEdgeTap, onBackgroundTap });
  cbRef.current = { onNodeTap, onEdgeTap, onBackgroundTap };

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

  // Sync elements: add new, drop removed, update data on the rest. Relayout only
  // when the node/edge SET changes — a tier recolour just updates data in place.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const incoming = new Set(elements.map((e) => String(e.data!.id)));
    let changed = false;
    cy.batch(() => {
      cy.elements().forEach((el) => {
        if (!incoming.has(el.id())) {
          el.remove();
          changed = true;
        }
      });
      elements.forEach((el) => {
        const existing = cy.getElementById(String(el.data!.id));
        if (existing.empty()) {
          cy.add(el);
          changed = true;
        } else {
          existing.data(el.data!);
        }
      });
    });
    if (changed) {
      const n = cy.nodes().length;
      if (n === 2) {
        // Flow-fan view: position the pair compactly, then fit zoomed-in & clear of
        // the drawer (no force layout needed for two fixed endpoints).
        placeFlowNodes(cy);
        fitFlowView(cy);
      } else {
        cy.layout(layoutFor(n)).run();
      }
    }
  }, [elements]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      n.data("label", nodeLabel(n.data(), labelMode));
    });
  }, [labelMode, elements]);

  return <div ref={containerRef} className="graph" />;
}
