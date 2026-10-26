# Handoff: state of the project, context and to-dos

This folder holds what a new person, or a new Claude Code session on another laptop, needs to
pick up the work without the conversations that produced it: what was built and why, how
the environments behave, what was learned the hard way, and what is left to do. It
complements the user documentation (the [README](../../README.md), [SETUP.md](../SETUP.md),
[deployment.md](../deployment.md), [troubleshooting.md](../troubleshooting.md),
[development.md](../development.md), [admin-portal.md](../admin-portal.md),
[demo-script.md](../demo-script.md)), which stays the reference for how things work. This
folder records where things stand and why they are the way they are.

Last updated: 2026-10-26 evening, after the avatar switch (several avatars in the UI).

## Reading order

| File | Read it for |
|---|---|
| [status-and-todo.md](status-and-todo.md) | **Start here.** What runs where today, what is verified, and the to-do list in priority order |
| [working-with-mohamed.md](working-with-mohamed.md) | How the owner works: approvals before every push, one command per step, zsh on the Mac, secrets never in chat |
| [architecture.md](architecture.md) | The components and how a text or voice turn flows through them |
| [environments.md](environments.md) | The two kinds of cluster (RHDP sandbox with cluster-admin, the shared Red Hat TMM cluster with one project), CI/CD, registries, external accounts |
| [setup-and-deploy.md](setup-and-deploy.md) | `scripts/setup.sh` in depth (both paths, every step, state files, environment variables), `deploy.sh`, tests, and an operations runbook |
| [openspline.md](openspline.md) | The self-hosted avatar: design, faces, quality and GPUs, benchmarks, limits, the patch to its server |
| [avatar-research.md](avatar-research.md) | What could replace or join OpenSpline for a professional HD look: the search of October 2026, the decision, and the plan for the self-hosted track |
| [voice-pipeline.md](voice-pipeline.md) | Latency work and its measurements, noise and phantom turns, interruptions with an avatar, LiveKit Agents 1.8 behaviour |
| [lessons-and-gotchas.md](lessons-and-gotchas.md) | Everything that cost time once and should not cost it twice |
| [history.md](history.md) | What was done, in order, with commits and the reason for each |

Exact internal values for the Red Hat TMM cluster (console and API URLs, the project name,
group and subscription names) are not in this public repository. They are in
`docs/handoff/private/environment.md`, which `.gitignore` keeps out of git, and
`scripts/setup.sh` writes it from the cluster on any laptop after `oc login`.

**Moving to another laptop needs no copying.**
1. Clone the repository. On a Mac with Homebrew, `brew install openshift-cli` gives `oc` for
   the login; setup installs the other tools itself.
2. Log in with `oc`, then set the project with `oc project user-<name>`: a new kubeconfig
   has none (the commands are in [environments.md](environments.md#2-the-red-hat-tmm-shared-cluster-namespace-path)).
3. Run `scripts/setup.sh`. With a healthy demo already deployed it redeploys nothing; it
   checks and verifies.

It rebuilds `~/secrets.env` from the cluster's secrets, the avoided nodes from the
release, and the private environment notes (see [setup-and-deploy.md](setup-and-deploy.md#another-laptop)).

## For a Claude Code session

The repository's [CLAUDE.md](../../CLAUDE.md) points here and is loaded automatically. Before
changing anything:
1. Read [status-and-todo.md](status-and-todo.md) and
   [working-with-mohamed.md](working-with-mohamed.md).
2. Check `git log --oneline -15` against the commits in [history.md](history.md); `main` may
   have moved since this was written.
3. Keep this folder current: when you finish something, move it from the to-do list into
   [history.md](history.md), and add whatever you learned to
   [lessons-and-gotchas.md](lessons-and-gotchas.md).
