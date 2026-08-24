# 🗂️ Developer Workspace Manager

A **local-first, zero-dependency** dashboard for the folder where all your projects live.
Point it at that directory once, and every subfolder becomes a managed project:

- 💾 **Save workspaces** — named project directories with per-project settings
- 🖥 **Start dev servers** — auto-detected commands (`npm run dev`, `cargo run`, `manage.py runserver`, …), live logs, one-click stop
- 🐳 **Docker Compose** — up / down / status per project (detected from `compose.yaml`)
- ⌨️ **Terminals & editors** — open your terminal emulator or editor in any project folder
- 🌿 **Git at a glance** — branch, ahead/behind, staged / modified / untracked counts, last commit
- 🔌 **Ports** — see which ports each dev server is listening on, click to open

Apple-inspired UI with light & dark mode, custom controls, no frameworks.

## Lightweight & private by design

- **Zero dependencies.** Backend is pure Python standard library; frontend is vanilla JS/CSS. No `node_modules`, no build step, no packages to audit.
- **Single file build.** The whole app ships as one `devws.pyz` (~30 KB).
- **Local only.** Binds to `127.0.0.1`. Makes **no outbound network connections** — no telemetry, no CDN assets, no update checks. Your config is a single JSON file in `~/.config/dev-workspace-manager/`.

## Install

**Option 1 — download a build** (no install): grab `devws.pyz` from the
[latest release](../../releases/latest) and run it:

```sh
python3 devws.pyz
```

**Option 2 — pip:**

```sh
pip install dev-workspace-manager
devws
```

**Option 3 — from source** (nothing to install):

```sh
git clone https://github.com/Mr-Don-Leo/linux-dev-workspace-manager
cd dev-workspace-manager
python3 -m devws
```

Then open **http://127.0.0.1:8765**, pick the folder that contains your projects, and you're set.

Requires Python ≥ 3.10 on Linux or macOS. Docker features light up automatically when the `docker` CLI is present; everything else works without it.

```
devws [--host 127.0.0.1] [--port 8765] [--config path/to/config.json]
```

> ⚠️ Keep the default host. The app starts processes on your machine, so it must not be exposed to a network.

## How it works

```
devws/
├── server.py            HTTP + JSON API (stdlib http.server, threaded)
├── services/
│   ├── config.py        atomic JSON workspace store
│   ├── processes.py     process orchestration: process groups, log ring buffers,
│   │                    SIGTERM→SIGKILL escalation
│   ├── gitinfo.py       git status via porcelain parsing
│   ├── dockerc.py       docker compose wrapper (injectable runner)
│   ├── ports.py         listening-port discovery via /proc/net/tcp
│   └── projects.py      project detection + terminal/editor launching
└── static/              vanilla JS/CSS frontend, Apple design language
```

## Tests

67 tests cover process orchestration, Git, Docker Compose, configuration,
project detection, port parsing, and the HTTP API end-to-end:

```sh
python3 -m unittest discover -s tests -v
```

Docker tests use an injected fake runner, so the suite passes on machines
without Docker. CI runs on Python 3.10 / 3.12 / 3.14.

## Contributing

Issues and PRs welcome. Branch model: feature branches → `dev` → `main`.
Please keep the zero-dependency rule — if it needs `pip install`, it doesn't go in.

## License

[MIT](LICENSE)
