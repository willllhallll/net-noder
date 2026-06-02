import { useIsLoading } from "../loading";

// The global, always-present transition indicator: a thin indeterminate progress bar
// (NProgress / Material "indeterminate linear progress" style) that animates whenever
// ANYTHING in the app is loading — a fetch, a cytoscape relayout, or a React transition.
// Driven entirely by the shared loading signal so it needs no props.
export default function LoadingBar() {
  const loading = useIsLoading();
  return (
    <div
      className={`loading-bar ${loading ? "is-active" : ""}`}
      role="progressbar"
      aria-hidden={!loading}
    >
      <div className="loading-bar-fill" />
    </div>
  );
}
