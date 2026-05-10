# Docs Refresh — Refresh Mode

You are auditing this repository's existing developer docs against the current code, and making **targeted updates** to fix every drift item the deterministic detectors found.

## Inputs (read these in order)

1. **`/tmp/drift-report.json`** — concrete drift items computed by `drift-detector.py`. **Every `high`-severity item MUST be addressed.** Each `medium`/`low` item must be addressed OR explicitly skipped with a reason.

2. **`/tmp/repo-context.json`** — full repo surface scan: tech stack, all package files, HTTP routes, env vars, ports, Vue/React routes, Docker services, gRPC services, scheduled tasks, top-level dirs.

3. **The full repo working tree** (Read/Glob/Grep available)

4. **Existing docs**: `README.md`, `CLAUDE.md`, `.claude/CLAUDE.md` (if present)

## Files you may modify

- `README.md`
- `CLAUDE.md`
- `.claude/CLAUDE.md` (if it exists)
- `docs/**/*.md` (if such files exist)

**You MUST NOT** modify any other file. The workflow stages tracked changes
only (`git add -u`), so untracked files outside the allow-list won't be
committed — but don't write them in the first place.

## Hard rule: address every high-severity drift item

The `drift_items` array has objects like:
```json
{
  "id": "DRIFT_017",
  "kind": "missing_envvar",
  "value": "CENTRIFUGO_TOKEN_SECRET",
  "explanation": "Source code reads this env var but no doc mentions it",
  "severity": "high"
}
```

For each drift item:
- **`high` severity** — fix the docs. The new env var must be documented, the stale command must be removed, the wrong port must be corrected. No exceptions.
- **`medium` severity** — fix unless there's a real reason not to (e.g., the missing dir is intentionally undocumented because it's internal-only).
- **`low` severity** — same.

## Audit workflow

1. **Read** `/tmp/drift-report.json` first. Count the items by severity.

2. **Read** the existing docs in full (`README.md` and `CLAUDE.md`). Take note of structure, voice, level of detail, ordering.

3. **Read** `/tmp/repo-context.json` for the full code surface (don't ask Claude to discover it — it's all there).

4. **For each high-severity drift item**, locate the right place in the docs and fix it. Examples:
   - `missing_envvar` → add to env-vars table or documented env-var list
   - `stale_envvar` → remove from docs (or note as deprecated if still relevant)
   - `stale_command` → remove or correct
   - `missing_command` → add to build/test/dev section
   - `stale_port` → correct
   - `missing_port` → add to ports section
   - `stale_dir` → remove from project-structure listing
   - `undocumented_dir` → add a one-line entry in the project-structure listing

5. **Beyond the drift list**, also check:
   - HTTP routes table (if docs have one) — does it match `http_routes` in the context?
   - Docker services list — match `docker_services`?
   - Vue/React pages list — match `vue_pages` / `react_routes`?
   - Tech stack versions — match the package files?

6. **Use Grep/Read freely** to verify any specific claim before writing it.

## Conservatism rules (still apply)

- **Don't rewrite for style.** Match the existing voice.
- **Don't reorder sections** unless the underlying structure changed.
- **Don't add new top-level sections** unless the underlying code clearly warrants one.
- **Never invent.** If a claim has no code evidence, leave it as `[VERIFY: <what to check>]`.
- **No hallucinated commands, file paths, env vars, or ports.** Every concrete identifier must be backed by something you grep'd or read or saw in the context JSON.
- **No emojis** unless the existing docs use them.

## What NOT to do

- Don't update sections that are still accurate, even if you would phrase them differently.
- Don't add changelogs, "recent changes" lists, or commit summaries.
- Don't touch files outside the allow-list above.
- Don't add Co-Authored-By, Signed-off-by, or any AI authorship attribution.
- Don't modify `.gitignore`, source code, configs, or workflow files.

## When you're done

Stop. The workflow will stage tracked changes (`git add -u`), commit, and push
directly to the trigger branch. There is no PR, no second-pass reviewer, and
no auto-merge gate — your edits land on `dev`/`main` as soon as you exit.

Be thorough on accuracy, conservative on style. The drift report tells you what to focus on — focus.
