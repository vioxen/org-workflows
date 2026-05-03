## Automated Docs Refresh

This PR was opened by `.github/workflows/docs-refresh.yml`. It refreshes `README.md` and `CLAUDE.md` against the current code state.

### Scope guard

Only these files may be modified by this workflow (CI rejects the PR if anything else is touched):

- `README.md`
- `CLAUDE.md`
- `.claude/CLAUDE.md`
- `docs/**`
- `.claude/rules/**`

### Auto-merge policy

| Target | Mode      | Auto-merge?  |
| ------ | --------- | ------------ |
| `dev`  | refresh   | yes — gated on AI review verdict + scope guard + ≤500 added lines |
| `dev`  | bootstrap | no — always draft, human review required |
| `main` | any       | no — always draft (dev → main is human-promoted) |

### What to look for during review

- Spot-check 2-3 concrete claims (commands, file paths, env vars, versions) against the actual code.
- Search for `[VERIFY:` markers — those are spots Claude flagged as uncertain.
- If you see anything wrong, comment + close the PR, or push fixes onto the branch.

### Re-running

To re-trigger manually: Actions → Docs Refresh → Run workflow → choose `refresh` or `bootstrap`.

If this PR was generated in `bootstrap` mode, all sections need a careful first read-through — the source had no prior docs to anchor against.
