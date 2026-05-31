import type {
  ConnectionFlows,
  Graph,
  Layer,
  NodeT,
  Stats,
} from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
  }
  return (await r.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  stats: () => req<Stats>("/api/stats"),
  layers: () => req<Layer[]>("/api/layers"),
  graph: () => req<Graph>("/api/graph"),
  node: (ip: string) => req<NodeT>(`/api/node/${enc(ip)}`),
  neighbors: (ip: string, limit: number) =>
    req<Graph>(`/api/node/${enc(ip)}/neighbors?limit=${limit}`),
  connectionFlows: (a: string, b: string) =>
    req<ConnectionFlows>(`/api/connection/flows?a=${enc(a)}&b=${enc(b)}`),
  search: (q: string) => req<NodeT[]>(`/api/search?q=${enc(q)}`),
};
