---
name: dev
description: Launch the net-noder dev environment for testing — starts the netnoder API (FastAPI :8000) then the web dev server (Vite :5173), in the background, and waits until both are reachable. Use when asked to /dev, start, boot, or bring up the app.
---

# Run the net-noder dev environment

Bring the full local stack up for manual testing: the **API** first, then the **web dev server**, both detached so they keep running after this turn.

## Steps

1. Run the launcher from the repo root:

   ```bash
   bash scripts/dev.sh
   ```

   It starts both services in their own process groups, writes PIDs/logs to `.run/`, and polls until each is reachable. Re-running is safe — already-running services are left alone.

2. Report the result to the user:
   - If both came up: tell them the **Web UI is at http://localhost:5173** (this is what to open for testing; it proxies `/api` to the API) and the API/OpenAPI is at http://localhost:8000/docs.
   - If a service failed readiness, the script prints the tail of its log. Read the relevant log (`.run/api.log` or `.run/web.log`) and surface the actual error.

## Notes

- Stop everything with the `/kill` skill (or `bash scripts/stop.sh`).
- The API needs an ingested DuckDB store to show data. If the graph is empty, the user likely hasn't run `netnoder-ingest` yet — mention it, don't ingest on your own.
- Override the API port with `NETNODER_PORT` if 8000 is taken.
