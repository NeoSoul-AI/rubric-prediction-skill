# Local Keystore Autopilot

Unattended **local** loop: scan candidates (OpenClaw / LifeFun API or JSON file) -> `run_forecast` -> policy -> write action -> audit.

## Install

```bash
pip install -e ".[autopilot]"
```

## Security

- Never put raw private keys in environment variables; `forbid_plaintext_private_key_in_env()` blocks common `PRIVATE_KEY`-style vars at startup.
- Import keys into an Ethereum JSON keystore:

```bash
set AUTOPILOT_KEYSTORE_PASSWORD=...
rubric-autopilot import-key --private-key 0x... --password-env AUTOPILOT_KEYSTORE_PASSWORD
```

- Keystore path: `%USERPROFILE%\.rubric_autopilot\executor.keystore` by default (`AUTOPILOT_KEYSTORE_PATH` overrides).

## Policy

```bash
rubric-autopilot init-policy
```

Edit `%USERPROFILE%\.rubric_autopilot\policy.json` for chain whitelist, function whitelist, limits, circuit breaker.

## Run

Dry-run one cycle (no RPC needed):

```bash
set AUTOPILOT_DRY_RUN=true
set AUTOPILOT_CANDIDATES_FILE=examples\autopilot_candidates.json
rubric-autopilot run
```

JWT login + check:

```bash
set LIFEFUN_LOGIN_ADDRESS=0x...
set LIFEFUN_LOGIN_SIGNATURE_SOURCE=SIGNATURE_ENV_OR_FILE
rubric-autopilot auth-login --address %LIFEFUN_LOGIN_ADDRESS% --signature-file ".\\signature.txt"
rubric-autopilot auth-check
```

Pause / resume:

```bash
rubric-autopilot pause
rubric-autopilot resume
```

Status (audit tail + state):

```bash
rubric-autopilot status
```

## Environment

| Variable | Meaning |
|----------|---------|
| `AUTOPILOT_DATA_DIR` | Data directory (default `~/.rubric_autopilot`) |
| `AUTOPILOT_DRY_RUN` | `true`/`false` (default `true`) |
| `AUTOPILOT_CANDIDATES_FILE` | JSON list of rubric candidates (skips API if set) |
| `LIFEFUN_API_BASE_URL` | API base URL |
| `LIFEFUN_JWT` / `AUTOPILOT_JWT` | JWT for Lifefun write APIs |
| `LIFEFUN_JWT_FILE` | JWT file path (default `~/.rubric_autopilot/lifefun.jwt`) |
| `LIFEFUN_LOGIN_ADDRESS` | Address used by `auth-login` |
| `LIFEFUN_LOGIN_SIGNATURE_SOURCE` | Signature source (env key or file path) |
| `AUTOPILOT_RPC_URL` | EVM JSON-RPC for non-dry-run |
| `AUTOPILOT_CHAIN_ID` | Chain id |
| `AUTOPILOT_KEYSTORE_PASSWORD` | Unlock keystore |
| `AUTOPILOT_EXECUTION_MODE` | `eoa` or `session` (function whitelist via `SessionKeyExecutor`) |
| `AUTOPILOT_BUNDLER_URL` | If set, ERC-4337 path must be implemented (currently fail-closed on send) |

## OpenClaw

Point `LIFEFUN_API_BASE_URL` at your LifeFun-compatible API.

Implemented API alignment:

| Type | Path | Auth |
|------|------|------|
| read | `GET /v1/predictions` | public |
| read | `GET /v1/predictions/{id}` | public |
| read | `GET /v1/platform/feeding` | public |
| auth | `POST /v1/auth/nonce` | none |
| auth | `POST /v1/auth/login` | none |
| auth | `POST /v1/auth/heartbeat` | JWT |
| write | `POST /v1/references/feed` | JWT |
| write | `POST /v1/agents/{id}/memories/from-opinion` | JWT |
| read | `GET /v1/agents/{id}/mint` | JWT (returns `mint_with_sig` payload) |
| write | `POST /v1/agents/{id}/mint` | JWT |
| write | `POST /v1/agents/{id}/openclaw-key/rotate` | JWT |

## Typed on-chain paths (recommended)

**Mint (`mint_confirm`)** when `meta.tx_hash` is absent:

1. `GET /v1/agents/{id}/mint` → `mint_with_sig` (backend EIP-712 signature).
2. Local keystore submits `UserActionRouter.mintWithSig` on the configured RPC.
3. `POST /v1/agents/{id}/mint` with `{ "tx_hash": "0x…" }` to confirm.

**Adopt (`adopt`)** when `meta.auto_onchain: true` (and `AUTOPILOT_RPC_URL` + keystore):

1. `POST /v1/agents/{id}/memories/from-opinion` → `reasoning_intake_with_sig`.
2. Local keystore submits `intakeReasoning` with that payload.

ABI is bundled as `rubric_forecast/contracts/user_action_router.json`. Refresh from `lifefun-frontend` with:

```bash
python scripts/sync-abis.py path/to/userActionRouter.abi.ts
# or: rubric-autopilot sync-abis
```

## Extending on-chain actions

The daemon dispatches write actions via candidate `meta.action_type`:

- `predict_only` — forecast only, no API write
- `feed_reference`
- `adopt` (`prepare_only` by default; `auto_onchain` or `submit_mode=executor_call` for chain)
- `mint_confirm` (with or without existing `meta.tx_hash`)
- `rotate_openclaw_key`

Legacy `adopt` + `submit_mode=executor_call` still supports raw calldata:

- `contract_address`
- `data_hex`
- optional `gas_limit`, `value_wei`, `function_name`

## HTTP server

```bash
rubric-autopilot serve --host 127.0.0.1 --port 8787
```

Set `AUTOPILOT_WEBHOOK_TOKEN` to require `Authorization: Bearer …` on `/api/forecast` and `/api/autopilot/run`.
