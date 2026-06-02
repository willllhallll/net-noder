import { useSyncExternalStore } from "react";

// A single, generic "is anything transitioning?" signal for the whole app. Any source
// of perceptible lag — an in-flight fetch, a cytoscape relayout, a React transition —
// brackets its work with beginLoad()/endLoad(); the count is the number of overlapping
// such operations. The UI (top progress bar) subscribes via useIsLoading().
//
// This is the hand-rolled equivalent of react-query's useIsFetching(), built on React
// 18's useSyncExternalStore — the recommended primitive for reading an external store.

let count = 0;
const listeners = new Set<() => void>();

function emit(): void {
  for (const l of listeners) l();
}

export function beginLoad(): void {
  count += 1;
  emit();
}

export function endLoad(): void {
  // Clamp at zero so an unbalanced end() can never drive the count negative and strand
  // the indicator "on" (or off) — the signal is purely > 0 / not.
  count = Math.max(0, count - 1);
  emit();
}

// Bracket a promise: begin before, end on settle (success OR failure). The try/finally
// guarantees the count is balanced even when the awaited work throws.
export async function track<T>(p: Promise<T>): Promise<T> {
  beginLoad();
  try {
    return await p;
  } finally {
    endLoad();
  }
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getSnapshot(): number {
  return count;
}

export function useIsLoading(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot) > 0;
}

// Resolve on the SECOND animation frame from now, i.e. after the browser has painted the
// current DOM. The first rAF lands at the start of the next frame (before its paint); the
// second lands after that frame's paint has been flushed to screen. Code that follows the
// await is therefore guaranteed to run only once the latest render is visible — the hook
// for "show a loading state, let it paint, THEN start main-thread-blocking work".
export function afterPaint(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}
