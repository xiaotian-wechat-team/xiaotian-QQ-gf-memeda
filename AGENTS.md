# AGENTS.md — Public portfolio snapshot

This repository is a sanitized snapshot of Xiaotian, a proactive AI companion agent. It is not the private production deployment.

## Read first

- `README.md`: public scope, demo, and verification commands.
- `ARCHITECTURE.md`: current module boundaries and delivery semantics.
- `data/persona_dossier.json`: fictional persona source of truth for behavior changes.

## Development

Use Python 3.11+ and run commands from the repository root unless a tool documents otherwise.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
cp config.example.json config.json
.venv/bin/python -m pytest -q
.venv/bin/python tools/onebot_qq_run.py --self-test
./scripts/demo_onebot.sh
```

Source modules use top-level imports from `src/`; retain that convention unless the launch model is changed everywhere. Configuration paths resolve from the repository root or the explicitly configured runtime directory.

## Safety and privacy

- Tests and demos must not contact real QQ/WeChat accounts, operate WeChat UI, or require private credentials.
- Use temporary directories and simulated transports for persistence and delivery tests. Mock OneBot listens only on loopback addresses.
- Do not commit `config.json`, real `.env` files, channel credentials, runtime databases, logs, chat exports, UI captures, or personal reference images.
- Keep fictional demo identities such as `demo_contact`; do not replace them with real user information.
- Real sending and UI automation are outside the public verification path. Do not turn a demo into a live send without an explicit user request.

## Changes

Keep changes focused and match the surrounding style. Update `README.md`, `ARCHITECTURE.md`, and affected documentation when behavior or verification commands change. Run the full test suite, OneBot self-test, and local demo before publishing a public snapshot.
