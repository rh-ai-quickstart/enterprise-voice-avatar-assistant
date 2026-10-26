# CLAUDE.md

An enterprise voice and text assistant with a talking avatar on Red Hat OpenShift AI:
- a RAG API, ingestion and a voice agent (Python, uv);
- a React frontend with an admin portal;
- n8n workflows;
- one Helm chart.

## Read first
- [docs/handoff/README.md](docs/handoff/README.md): the state of the project, the to-do
  list, the environments, and what was learned. Start with
  [status-and-todo.md](docs/handoff/status-and-todo.md) and
  [working-with-mohamed.md](docs/handoff/working-with-mohamed.md).
- User documentation: [README.md](README.md), [docs/SETUP.md](docs/SETUP.md),
  [docs/deployment.md](docs/deployment.md), [docs/troubleshooting.md](docs/troubleshooting.md),
  [docs/development.md](docs/development.md).

## Rules that are not in the code
- **Ask before every push to `main`**, with the commit SHA. Rebase on `origin/main` first:
  CI commits image promotions there after every image build.
- **Never ask for a key or token in the chat, and never print one.** Keys go through
  `scripts/setup.sh` step 5 (hidden input) into `~/secrets.env`.
- **Commands for Mohamed:** one step per message, for zsh on a Mac. His default kubeconfig
  (`~/.kube/config`) has the TMM cluster with his project current, so commands need no
  `KUBECONFIG` (`~/.kube/tmm` is a link to it). Secrets go through `read -rs "VAR?prompt: "`,
  and nothing touches the end of a URL.
- **This repository is public.** Internal cluster hostnames, user and group names stay out
  of git (`docs/handoff/private/` is gitignored).
- **Self-hosted first.** Keep latency at 1 to 2 s from the end of speech to the first audio.
- **Keep [docs/handoff/](docs/handoff/) current** when you finish or learn something.

## Checks before committing

| Part | Commands |
|---|---|
| `services/rag-api`, `services/ingestion`, `services/voice-agent` | `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` |
| `frontend` | `npm ci && npm run typecheck && npm test && npm run build` |
| `chart` | `helm lint chart -f chart/values-demo-cluster.yaml`, plus `helm template` with `--set voiceAgent.avatarProvider=openspline` (and `tavus`, `none`) |
| `scripts` | `bash -n scripts/<name>.sh` |
