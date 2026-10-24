# Working with Mohamed

Mohamed Heiba owns the frontend and the n8n workflows, and drives the demo. Sander owns the
infrastructure; the backend is shared. These are the working agreements built up over many
sessions. Follow them; they are not in the code.

## Approvals
- **Every push to `main` needs an explicit go**, in the form "Yes, push <sha> to main".
  Commit locally, show what changed, ask, then push only that commit. An approval covers
  that push only. The repository has no PR requirement and no auto-merge. Recent work went
  straight to `main` at Mohamed's request; earlier features went through PRs (#6 to #11).
- Before pushing, `git fetch` and rebase on `origin/main`. CI's promote commits
  (`ci: deploy images sha-<7> ...`) land on `main` after every image build.
- Actions that are hard to undo or that reach outside: ask first, every time. That covers
  deleting cluster objects you did not create, posting to GitHub issues or upstream
  projects, uploading anywhere, and booking or releasing GPUs.
- Throwaway objects you created yourself for a test (pods, BuildConfigs, benchmark files)
  are yours to clean up. Say so in your reply.

## Commands for Mohamed to run
- **One step per message.** Give one pasteable command, wait for the output, then the
  next. Mohamed pastes outputs back and steers scope as things go.
- **One line by default.** Use a script only when a line would be unwieldy (manifests,
  multi-line Python). Prefer the repo's scripts (`scripts/setup.sh --step N`,
  `scripts/demo-preflight.sh`, `scripts/test-services.sh`) over ad hoc code.
- **The Mac runs zsh**, with oh-my-zsh:
  - Prompt for secrets with `read -rs "VAR?prompt: "`. In zsh `read -p` means coprocess and
    fails with "no coprocess".
  - Never let a character touch the end of a URL. url-quote-magic escapes a `;` pasted right
    after a URL, even inside quotes, which leaves a `for>` prompt. End the line with the URL,
    or follow it with a space and `&&`. Avoid `for ...; do ...; done` loops with a URL inside.
  - zsh does not split `$var` into words. Use arrays, or `bash -c`.
- **Start Mac commands with `export KUBECONFIG=~/.kube/tmm`.** Another terminal tab may point
  at another cluster. Run `oc whoami` before anything that sends the token
  (`oc whoami -t`).
- Mohamed runs setup from the main checkout: `~/redhat-code/enterprise-voice-avatar-assistant`.
  zsh aliases: `cd-enterprise-voice-avatar-assistant` goes there, and
  `cld-enterprise-voice-avatar-assistant` goes there and starts Claude Code. `cd-redhat-code`
  and `cld-redhat-code` do the same for the parent folder. The usual cycle after a push is:
  ```bash
  cd ~/redhat-code/enterprise-voice-avatar-assistant && git pull && export KUBECONFIG=~/.kube/tmm && scripts/setup.sh --step 6
  ```
- Prefer polling loops (`until ...; do sleep 10; done`) over fixed sleeps.

## Secrets
- **Never ask for a key or token in the chat, and never print one.** Keys go into
  `~/secrets.env` through setup step 5, which prompts with hidden input. Cluster tokens come
  from the console ("Copy login command"), entered with `read -rs`.
- `create-secrets.sh` keeps existing secrets. `FORCE=1` regenerates every password, n8n's
  encryption key included, which makes stored n8n credentials unreadable. Use
  `REFRESH=<secret>` for one secret, or `oc set data` for one key.
- The OpenShift login token on TMM expires after some hours. When `oc` answers
  "Unauthorized", ask Mohamed to log in again (command in
  [environments.md](environments.md#2-the-red-hat-tmm-shared-cluster-namespace-path)).

## Preferences that shape decisions
- **Self-hosted first.** "Ideally I prefer everything to be self-hosted." Lead with models
  and services on the cluster's GPUs; mention cloud services (Tavus, hosted APIs) as options
  with their trade-offs.
- **"100% automated in setup.sh."** A new environment or a new choice belongs in setup.sh,
  detected from the cluster, with an environment variable to override it, not in manual
  steps.
- **Keep both avatar options.** Tavus and OpenSpline stay; setup step 2 chooses.
- **Latency matters a lot.** The current 1 to 2 s from end of speech to first audio is the
  bar. Do not trade it away for picture quality without saying so: that is why high quality
  needs a GPU pair, and why it never pushes Kokoro off its GPU.
- **Incremental scope.** Deliver one piece, confirm, continue. Ask before fanning out to many
  files when the request is open.

## How to report
- Lead with the answer: what is now true, then what is not done or not verified.
- Give measurements, not adjectives, when there are numbers (latency, real-time factor, fps).
- When a fix is pushed, say what Mohamed has to run, and what to look for in the browser.
- A screenshot from Mohamed usually shows two things. Look for the second one: a repeated
  greeting or a phantom bubble next to the thing asked about.

## Repository conventions
- Commit messages: a subject that says what changed for a user or operator ("Voice: noise no
  longer becomes questions; ..."), then a body with the cause and the fix in plain words.
  End with the `Co-Authored-By:` trailer your harness gives you.
- Code and comments match the surrounding style. Comments say why, in full sentences, and
  are sparse.
- Each Python service runs `uv run ruff check .`, `uv run ruff format --check .` and
  `uv run pytest -q`. The frontend runs `npm run typecheck`, `npm test` and `npm run build`.
  The chart runs `helm lint chart -f chart/values-demo-cluster.yaml` and renders for each
  provider. CI runs all of these plus Playwright end-to-end tests and the n8n workflow tests.
- Docs live in `docs/` and the README. Update them with the change, in the same plain style.
- Work in a git worktree when your harness gives you one. Never use a bare `git stash`: the
  stash is shared between worktrees.
