# Web demo (local)

A **stdlib-only** HTTP server and static page call [`run_forecast`](../../rubric_forecast/engine.py) over HTTP for manual checks. It does **not** run the Autopilot daemon or expose unattended control endpoints.

## Run the forecast UI

From the repository root:

```bash
python examples/web_demo/server.py
```

Open `http://127.0.0.1:8765/`. The server binds to loopback only; do not expose it to untrusted networks.

- **POST `/api/forecast`** — JSON body → engine output (same contract as [SKILL.md](../../SKILL.md)).
- **GET `/sample-input`** — sample JSON used by the “Load sample input” button.
- **GET `/lifefun-candidates`** — returns [`examples/lifefun_candidates.json`](../lifefun_candidates.json) for read-write autopilot demos.
- **GET `/autopilot-env-example`** — plain-text copy of [`autopilot.env.example`](autopilot.env.example) (for the Autopilot section in the demo page).

## Autopilot (CLI, unattended)

The interactive page is only for one-off forecasts. For scan → forecast → policy → audit (and optional on-chain wiring in your fork), use the **`rubric-autopilot`** CLI. Install extras:

```bash
pip install -e ".[autopilot]"
```

Full setup, keystore, policy, and environment reference: **[AUTOPILOT.md](../../AUTOPILOT.md)**. A copy-pastable template lives in [`autopilot.env.example`](autopilot.env.example).

### Dry-run one cycle (PowerShell, Windows)

From the repo root, using a local candidates file (no RPC required):

```powershell
$env:AUTOPILOT_DRY_RUN = "true"
$env:AUTOPILOT_CANDIDATES_FILE = "$(Get-Location)\examples\lifefun_candidates.json"
rubric-autopilot run
```

Adjust `AUTOPILOT_CANDIDATES_FILE` if your working directory differs. On Unix:

```bash
export AUTOPILOT_DRY_RUN=true
export AUTOPILOT_CANDIDATES_FILE="$(pwd)/examples/lifefun_candidates.json"
rubric-autopilot run
```
