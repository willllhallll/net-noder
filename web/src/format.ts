export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 2 : 1)} ${u[i]}`;
}

export function fmtNum(n: number): string {
  return n.toLocaleString();
}

export function fmtTime(t: number | null): string {
  if (t == null) return "—";
  return new Date(t * 1000).toLocaleString();
}

export function nodeColor(kind: string, isLocal: boolean): string {
  if (kind === "multicast") return "#a855f7";
  if (kind === "broadcast") return "#ef4444";
  return isLocal ? "#3b82f6" : "#f59e0b";
}

export function castColor(cast: string): string {
  if (cast === "multicast") return "#a855f7";
  if (cast === "broadcast") return "#ef4444";
  return "#64748b";
}

export function nodeSize(bytes: number): number {
  return Math.max(10, Math.min(60, 8 + 7 * Math.log10(bytes + 10)));
}

export function edgeWidth(bytes: number): number {
  return Math.max(1, Math.min(12, 0.5 + 1.6 * Math.log10(bytes + 10)));
}
