---
name: kill
description: Gracefully stop the net-noder services started by /dev — the web dev server and the API. Use when asked to /kill, stop, shut down, or tear down the app.
---

# Kill net-noder

Gracefully shut down the services launched by `/dev`.

## Steps

1. Run the stopper from the repo root:

   ```bash
   bash scripts/stop.sh
   ```

   It reads the PID files in `.run/`, sends `SIGTERM` to each service's process group (so Vite's child processes go too), waits for a clean exit, and only escalates to `SIGKILL` if a process refuses to stop. Stale/missing PIDs are handled quietly.

2. Confirm to the user which services were stopped (the script prints a line per service).

## Notes

- Safe to run when nothing is up — it just reports there's nothing to stop.
- If the user reports a port is still held afterward, check for a stray process: `lsof -i :8000` / `lsof -i :5173`.
