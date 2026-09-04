# Rubric Forecasting

**Structured forecasting** tooling and Cursor Agent skill: the model interprets the question and organizes evidence; a Python engine performs weighting, normalization, and sensitivity analysis, and emits auditable JSON plus natural-language reasoning.

See [INTRODUCTION.md](INTRODUCTION.md) for full concepts and workflow.

## Requirements

- Python **3.10+** — core `rubric_forecast` engine is **stdlib-only**; optional autopilot extras add `eth-account` and `web3`.
- Tests and local dev: `pip install -e ".[dev]"` (below)

## Quick start (CLI)

From the repo root (ensure the `rubric_forecast` package is on the path):

```bash
python scripts/rubric_forecast.py --input examples/polymarket_hormuz_geopolitics_input.json
```

Or as a module (also works after install):

```bash
python -m rubric_forecast --input examples/polymarket_hormuz_geopolitics_input.json
```

Read from stdin:

```bash
cat examples/polymarket_hormuz_geopolitics_input.json | python -m rubric_forecast
```

After `pip install .`, you can use the console entry:

```bash
pip install .
rubric-forecast --input path/to/input.json
```

## Web demo (local)

A small stdlib-only server and static page call `run_forecast` over HTTP for quick manual checks:

```bash
python examples/web_demo/server.py
```

Open `http://127.0.0.1:8765/` — load sample input, edit JSON, then run. Bind address is loopback only; do not expose this process to untrusted networks.

## Cursor Skill

Add [SKILL.md](SKILL.md) to Cursor Agent Skills (or your team’s `.cursor` layout). The skill states: **do not hand-compute scores in chat**; numeric results must come from this repo’s engine, and output must include an `engine` field.

Concepts and I/O details: [INTRODUCTION.md](INTRODUCTION.md).

For OpenClaw production automation you can use **either** `lifefun-frontend` (`/api/autopilot/run`) **or** this repo’s `rubric-autopilot serve` (same route shapes). Keep deterministic scoring in `rubric_forecast.engine`.

## Local autopilot (OpenClaw / keystore)

Optional **unattended local loop**: discover (pluggable adapter) or candidates JSON → rubric scoring → policy → LifeFun API → optional `mintWithSig` / `intakeReasoning` on RPC. Requires extra deps:

```bash
pip install -e ".[autopilot]"
```

CLI / HTTP: `rubric-autopilot` (see [AUTOPILOT.md](AUTOPILOT.md)):

```bash
rubric-autopilot run --adapter lifefun --once
rubric-autopilot list-adapters
rubric-autopilot serve --port 8787
rubric-autopilot sync-abis   # refresh ABI from sibling lifefun-frontend path, or pass path
```

Register another dApp discovery module with setuptools:

```toml
[project.entry-points."rubric_forecast.adapters"]
myapp = "my_package.adapters:MyAdapter"
```

Implement `discover_candidates(ctx: AdapterContext) -> list[dict]` (see `rubric_forecast/adapters/base.py`).

LifeFun API alignment now includes:
- JWT auth flow: `/v1/auth/nonce`, `/v1/auth/login`, `/v1/auth/heartbeat`
- Read APIs: `/v1/predictions`, `/v1/predictions/{id}`, `/v1/platform/feeding`
- Write APIs: `/v1/references/feed`, `/v1/agents/{id}/memories/from-opinion`, `/v1/agents/{id}/mint`, `/v1/agents/{id}/openclaw-key/rotate`

## Repository layout

| Path | Description |
|------|-------------|
| [SKILL.md](SKILL.md) | Agent input contract, scoring rules, output JSON shape |
| [INTRODUCTION.md](INTRODUCTION.md) | Long-form reader intro and glossary |
| [rubric_forecast/engine.py](rubric_forecast/engine.py) | Deterministic forecasting engine |
| [scripts/rubric_forecast.py](scripts/rubric_forecast.py) | Compatibility entrypoint → package `engine` |
| [examples/](examples/) | Sample inputs, outputs, and notes |
| [examples/web_demo/](examples/web_demo/) | Local browser demo (`server.py` + `index.html`) |
| [AUTOPILOT.md](AUTOPILOT.md) | Local keystore autopilot setup |
| [rubric_forecast/daemon.py](rubric_forecast/daemon.py) | Scan → forecast → policy → actions |
| [rubric_forecast/adapters/](rubric_forecast/adapters/) | Pluggable DApp discovery (`lifefun` built-in) |
| [rubric_forecast/actions/](rubric_forecast/actions/) | `predict_only` / feed / adopt / mint / rotate handlers |
| [rubric_forecast/server/http.py](rubric_forecast/server/http.py) | Optional `/api/forecast` + `/api/autopilot/run` |
| [scripts/sync-abis.py](scripts/sync-abis.py) | Sync `userActionRouter` ABI from frontend TS |
| [config/evo_config.example.json](config/evo_config.example.json) | Template for EvoEvo / OpenClaw pipeline (see below) |

## EvoEvo / OpenClaw (optional)

Scripts under `scripts/` such as `evo_pipeline.py` and `evo_submit.py` read a JSON config. Copy the example and adjust paths and secrets:

```bash
cp config/evo_config.example.json config/evo_config.json
```

Point `wallets_file` at your wallet JSON. Copy the placeholder and fill in one wallet (the pipeline only ever submits from the first entry):

```bash
cp config/evo_wallets.example.json config/evo_wallets.json
```

Paths in the example are relative to the **repository root**; run those scripts from the repo root (or use absolute paths). Both `config/evo_config.json` and `config/evo_wallets.json` are gitignored so machine-specific values and keys are not committed.

Web-search evidence enrichment uses [Tavily](https://tavily.com). Export `TAVILY_API_KEY` before running; without it the pipeline still runs, but skips the Tavily source and records `tavily key missing` in the ingest notes.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)
