interface Props {
  // "sm" for inline use (e.g. the search dropdown); default is the canvas-overlay size.
  size?: "sm" | "md";
}

// A bare CSS border-spinner; all motion lives in styles.css (.spinner / @keyframes spin),
// which also honours prefers-reduced-motion.
export default function Spinner({ size = "md" }: Props) {
  return <span className={`spinner ${size === "sm" ? "spinner-sm" : ""}`} aria-hidden />;
}
