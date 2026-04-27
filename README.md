# Rubric Forecasting

**Structured forecasting** tooling and Cursor Agent skill: the model interprets the question and organizes evidence; a Python engine performs weighting, normalization, and sensitivity analysis, and emits auditable JSON plus natural-language reasoning.

See [INTRODUCTION.md](INTRODUCTION.md) for full concepts and workflow.

## Requirements

- Python **3.10+** (stdlib only at runtime; no third-party deps for execution)
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

## Repository layout

| Path | Description |
|------|-------------|
| [SKILL.md](SKILL.md) | Agent input contract, scoring rules, output JSON shape |
| [INTRODUCTION.md](INTRODUCTION.md) | Long-form reader intro and glossary |
| [rubric_forecast/engine.py](rubric_forecast/engine.py) | Deterministic forecasting engine |
| [scripts/rubric_forecast.py](scripts/rubric_forecast.py) | Compatibility entrypoint → package `engine` |
| [examples/](examples/) | Sample inputs, outputs, and notes |
| [examples/web_demo/](examples/web_demo/) | Local browser demo (`server.py` + `index.html`) |
| [config/evo_config.example.json](config/evo_config.example.json) | Template for EvoEvo / OpenClaw pipeline (see below) |

## EvoEvo / OpenClaw (optional)

Scripts under `scripts/` such as `evo_pipeline.py` and `evo_submit.py` read a JSON config. Copy the example and adjust paths and secrets:

```bash
cp config/evo_config.example.json config/evo_config.json
```

Point `wallets_file` at your wallet JSON. Paths in the example are relative to the **repository root**; run those scripts from the repo root (or use absolute paths). The file `config/evo_config.json` is gitignored so machine-specific values are not committed.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)
