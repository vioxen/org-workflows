# vioxen/org-workflows

Centralized reusable GitHub Actions workflows for the vioxen organization.

The point of this repo is **one canonical instance** of every cross-repo CI we run.
Each consuming repo only needs a tiny caller stub; the workflow logic, scripts,
and prompts live here.

## Workflows

### `docs-refresh.yml` — auto-refresh README + CLAUDE.md

Runs Claude after every merge to `dev` or `main`, regenerates docs against the
current code, opens PRs back to both branches. Auto-merges to `dev` when an AI
reviewer signs off; PRs to `main` are always opened as drafts (human-promoted).

- **Trigger** (in caller): `pull_request: { types: [closed], branches: [dev, main] }`
- **Required secret**: `ANTHROPIC_API_KEY`
- **Shared assets**: `scripts/context-scanner.py`, `scripts/drift-detector.py`,
  `prompts/docs-refresh.md`, `prompts/docs-bootstrap.md`, `prompts/docs-reviewer.md`,
  `prompts/pr-body.md` — all checked out into `$RUNNER_TEMP/org-workflows` at
  job start so they live in one place and update without per-repo PRs.

See `examples/caller-docs-refresh.yml` for the full caller stub.

### `discord-notify-main.yml` — merge-to-main Discord alerts

Posts a Discord embed every time a commit lands on `main`. Pinned-jq install,
intermediate env vars to neutralize commit-message injection, multibyte-safe
truncation, surfaces non-200 responses as job failures.

- **Trigger** (in caller): `push: { branches: [main] }`
- **Required secret**: `DISCORD_MAIN_PUSH_WEBHOOK`

See `examples/caller-discord-notify.yml` for the full caller stub.

## Adding a new reusable workflow

1. Add `.github/workflows/<name>.yml` with `on: workflow_call:` and explicit
   `inputs:` + `secrets:` blocks.
2. Add an example caller stub under `examples/`.
3. Document it above.
4. Open a PR to `main` (no `dev` branch in this repo — pure CI plumbing).

## Versioning

All caller stubs reference `@main`. There is no semver here yet — when we want
isolation for risky changes, branch off `main`, point one repo's caller stub at
the branch, validate, then merge.
