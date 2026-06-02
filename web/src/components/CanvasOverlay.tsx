import Spinner from "./Spinner";

interface Props {
  show: boolean;
}

// An in-place, graph-only loading veil: a subtle dim over the canvas with a centered
// spinner, shown while the graph itself is loading or relaying out (large fcose layouts
// can block for seconds). The global LoadingBar covers everything else; this gives the
// graph transition feedback right where the user is looking.
export default function CanvasOverlay({ show }: Props) {
  if (!show) return null;
  return (
    <div className="canvas-overlay">
      <Spinner />
      <span className="canvas-overlay-label">loading…</span>
    </div>
  );
}
