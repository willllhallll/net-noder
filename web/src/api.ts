import type {
  ConversationDetail,
  EphemeralPorts,
  Graph,
  Metric,
  NodeDetail,
  NodeT,
  Stats,
} from "./types";

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`${r.status} ${r.statusText}: ${await r.text()}`);
  }
  return (await r.json()) as T;
}

const enc = encodeURIComponent;

export const api = {
  stats: () => get<Stats>("/api/stats"),
  top: (metric: Metric, limit: number) =>
    get<Graph>(`/api/graph/top?metric=${metric}&limit=${limit}`),
  node: (ip: string) => get<NodeDetail>(`/api/node/${enc(ip)}`),
  neighbors: (ip: string, metric: Metric, limit: number) =>
    get<Graph>(`/api/node/${enc(ip)}/neighbors?metric=${metric}&limit=${limit}`),
  conversation: (a: string, b: string) =>
    get<ConversationDetail>(`/api/conversation?a=${enc(a)}&b=${enc(b)}`),
  ephemeralPorts: (
    a: string,
    b: string,
    proto: string,
    serverPort: number,
    cast: string
  ) =>
    get<EphemeralPorts>(
      `/api/conversation/ports?a=${enc(a)}&b=${enc(b)}&proto=${enc(proto)}` +
        `&server_port=${serverPort}&cast=${enc(cast)}`
    ),
  search: (q: string) => get<NodeT[]>(`/api/search?q=${enc(q)}`),
};
