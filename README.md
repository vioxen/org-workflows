# vioxen/org-workflows

Centralized reusable GitHub Actions workflows for the vioxen organization.

The point of this repo is **one canonical instance** of every cross-repo CI we run.
Each consuming repo only needs a tiny caller stub; the workflow logic, scripts,
and prompts live here.

## Workflows

### `docs-refresh.yml` — auto-refresh README + CLAUDE.md

Runs Claude after every merge to `dev` or `main`, regenerates docs against the
current code, and pushes the updated `README.md` / `CLAUDE.md` directly to the
trigger branch. No PRs, no reviewer, no scope/size gates — the bot needs push
permission on `dev`/`main` (admin PAT via `QUANTA_PIPELINE_TOKEN`, or no branch
protection on the target branch).

- **Trigger** (in caller): `pull_request: { types: [closed], branches: [dev, main] }`
- **Required secret**: `ANTHROPIC_API_KEY_CI`
- **Optional secret**: `QUANTA_PIPELINE_TOKEN` (admin PAT to bypass non-admin
  branch protection on `dev`/`main`)
- **Shared assets**: `scripts/context-scanner.py`, `scripts/drift-detector.py`,
  `prompts/docs-refresh.md`, `prompts/docs-bootstrap.md` — checked out into
  `$RUNNER_TEMP/org-workflows` at job start so they live in one place and
  update without per-repo PRs.

See `examples/caller-docs-refresh.yml` for the full caller stub.

### `discord-notify-main.yml` — merge-to-main Discord alerts

Posts a Discord embed every time a commit lands on `main`. Pinned-jq install,
intermediate env vars to neutralize commit-message injection, multibyte-safe
truncation, surfaces non-200 responses as job failures.

- **Trigger** (in caller): `push: { branches: [main] }`
- **Required secret**: `DISCORD_MAIN_PUSH_WEBHOOK`

See `examples/caller-discord-notify.yml` for the full caller stub.

### `gitleaks.yml` — secret scanning gate

Blocks credentials from reaching a repo's default branch. On a PR it scans only
the commits the PR adds (merge-base..head), so a repo with known historical
leaks can adopt the gate without every unrelated PR turning red.

- **Trigger** (in caller): `pull_request` + a weekly `schedule`
- **Required secret**: none
- **Shared asset**: `.gitleaks.toml` — the org allowlist, fetched to
  `$RUNNER_TEMP` at job start, so tuning it is one PR here instead of one per repo.
- **Key inputs**: `fail_on_leak` (report-only soak), `full_history` (off by
  default — expected to be red on QUANTATECH-446-affected repos until their
  rotations land), `config_path` (repo-local override).

Uses a pinned, checksum-verified gitleaks release binary rather than
`gitleaks/gitleaks-action`, which requires a **paid licence for
organization-owned repos**. Same scanner, no licence surface.

`scripts/gitleaks-selftest.sh` asserts the config in both directions — planted
secrets must fail, the known false-positive classes must stay clean — and runs
in CI (`gitleaks-selftest.yml`) on any PR touching the gate. An allowlist that
is only ever tested against false positives eventually suppresses everything
and goes permanently green while leaking.

See `examples/caller-gitleaks.yml` for the full caller stub.

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
