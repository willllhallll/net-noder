import type {
  ConnectionDetail,
  Graph,
  NodeDetail,
  NodeT,
  ReplyPorts,
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
  graph: () => get<Graph>("/api/graph"),
  node: (ip: string) => get<NodeDetail>(`/api/node/${enc(ip)}`),
  neighbors: (ip: string, limit: number) =>
    get<Graph>(`/api/node/${enc(ip)}/neighbors?limit=${limit}`),
  connection: (a: string, b: string) =>
    get<ConnectionDetail>(`/api/connection?a=${enc(a)}&b=${enc(b)}`),
  replyPorts: (
    a: string,
    b: string,
    proto: string,
    serverPort: number,
    cast: string,
    serverIsA: boolean
  ) =>
    get<ReplyPorts>(
      `/api/conversation/ports?a=${enc(a)}&b=${enc(b)}&proto=${enc(proto)}` +
        `&server_port=${serverPort}&cast=${enc(cast)}&server_is_a=${serverIsA}`
    ),
  search: (q: string) => get<NodeT[]>(`/api/search?q=${enc(q)}`),
};
