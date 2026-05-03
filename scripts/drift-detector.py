#!/usr/bin/env python3
"""
Drift detector for docs-refresh workflow.

Reads the context-scanner output (`/tmp/repo-context.json`) and the
existing docs (README.md, CLAUDE.md, .claude/CLAUDE.md), then computes
**concrete drift items** that the LLM refresher must address.

Output: `/tmp/drift-report.json` with shape:

{
  "drift_items": [
    { "id": "STALE_COMMAND_1", "kind": "stale_command", "value": "npm run dev:legacy",
      "explanation": "Documented but not in any package.json scripts", "severity": "high" },
    ...
  ],
  "stats": { "stale_commands": 1, "missing_commands": 3, ... },
  "doc_files_inspected": ["README.md", "CLAUDE.md"]
}

Severity levels:
  high   — likely to mislead a developer (command, env var, port)
  medium — informational drift (new dir, new dependency)
  low    — possibly intentional omission (long list of routes, etc.)

The refresher prompt requires Claude to address every `high` item and
explicitly skip-or-resolve every `medium`/`low` item.

No third-party dependencies. Python 3.10+.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())).resolve()
CONTEXT_PATH = Path(os.environ.get("REPO_CONTEXT_PATH", "/tmp/repo-context.json"))
OUT_PATH = Path(os.environ.get("DRIFT_REPORT_PATH", "/tmp/drift-report.json"))

DOC_PATHS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "CLAUDE.md",
    REPO_ROOT / ".claude" / "CLAUDE.md",
]


# ---------- doc parsing ----------

def load_docs() -> tuple[str, list[str]]:
    """Return concatenated doc text + list of doc files actually read."""
    parts: list[str] = []
    files: list[str] = []
    for p in DOC_PATHS:
        if p.exists():
            try:
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
                files.append(str(p.relative_to(REPO_ROOT)))
            except Exception:
                continue
    return "\n\n".join(parts), files


def extract_doc_commands(text: str) -> set[str]:
    """Pull command lines from shell code blocks: npm run X, cargo Y, etc."""
    commands: set[str] = set()
    # Code blocks: ```...```  with optional language hint
    for block in re.findall(r"```(?:[a-zA-Z]*)?\n(.*?)\n```", text, flags=re.S):
        for line in block.splitlines():
            line = line.strip().lstrip("$").strip()
            if not line or line.startswith("#"):
                continue
            # npm/yarn/pnpm/bun run NAME
            m = re.match(r"^(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?([\w:.-]+)", line)
            if m:
                commands.add(f"npm run {m.group(1)}" if "run" in line else line.split()[0] + " " + m.group(1))
            # cargo NAME
            m = re.match(r"^cargo\s+([\w-]+)", line)
            if m:
                commands.add(f"cargo {m.group(1)}")
            # python -m / python script
            m = re.match(r"^python3?\s+-m\s+([\w.]+)", line)
            if m:
                commands.add(f"python -m {m.group(1)}")
    # Inline-backtick commands
    for code in re.findall(r"`([^`]{3,80})`", text):
        m = re.match(r"^(?:npm|yarn|pnpm|bun)\s+(?:run\s+)?([\w:.-]+)$", code.strip())
        if m:
            commands.add(f"npm run {m.group(1)}")
    return commands


def extract_doc_envvars(text: str) -> set[str]:
    """Pull env var names: typically ALL_CAPS_WITH_UNDERSCORES, often in backticks."""
    found: set[str] = set()
    # Backticked
    for code in re.findall(r"`([A-Z][A-Z0-9_]{2,40})`", text):
        found.add(code)
    # In tables: | VAR_NAME |
    for m in re.finditer(r"\|\s*([A-Z][A-Z0-9_]{2,40})\s*\|", text):
        found.add(m.group(1))
    # In env-block code fences: KEY=value
    for block in re.findall(r"```(?:bash|sh|env|dotenv)?\n(.*?)\n```", text, flags=re.S):
        for line in block.splitlines():
            m = re.match(r"^export\s+([A-Z][A-Z0-9_]{2,40})=", line.strip())
            if m:
                found.add(m.group(1))
            m = re.match(r"^([A-Z][A-Z0-9_]{2,40})=", line.strip())
            if m:
                found.add(m.group(1))
    return found


def extract_doc_ports(text: str) -> set[int]:
    found: set[int] = set()
    for m in re.finditer(r":(\d{4,5})\b", text):
        try:
            p = int(m.group(1))
            if 1000 <= p <= 65535:
                found.add(p)
        except ValueError:
            pass
    for m in re.finditer(r"(?i)\bport[:\s=]+(\d{2,5})", text):
        try:
            p = int(m.group(1))
            if 1000 <= p <= 65535:
                found.add(p)
        except ValueError:
            pass
    return found


def extract_doc_dirs(text: str) -> set[str]:
    """Backticked or fenced top-level directory references like `src/` or `aux/`."""
    found: set[str] = set()
    for code in re.findall(r"`([a-zA-Z][\w./-]*?/)`", text):
        # Just the top-level segment
        seg = code.split("/")[0]
        if seg and not seg.startswith(".") and "/" not in seg:
            found.add(seg)
    return found


# ---------- code-side extraction (from context-scanner output) ----------

def code_commands(ctx: dict) -> set[str]:
    """All scripts from package.json files and pyproject scripts and Cargo bin names."""
    out: set[str] = set()
    for pkg in ctx.get("package_files", []):
        if pkg.get("ecosystem") == "node":
            for name in (pkg.get("scripts") or {}).keys():
                out.add(f"npm run {name}")
        elif pkg.get("ecosystem") == "rust":
            for binary in pkg.get("binaries") or []:
                out.add(f"cargo run --bin {binary}")
            out.add("cargo build")
            out.add("cargo test")
        elif pkg.get("ecosystem") == "python":
            for script in pkg.get("scripts") or []:
                out.add(script)
    return out


def code_envvars(ctx: dict) -> set[str]:
    return set(ctx.get("env_vars_referenced", []))


def code_ports(ctx: dict) -> set[int]:
    return set(ctx.get("ports_referenced", []))


def code_dirs(ctx: dict) -> set[str]:
    return {d["name"] for d in ctx.get("top_level_dirs", []) if d.get("files", 0) >= 3}


# ---------- main ----------

def main() -> None:
    if not CONTEXT_PATH.exists():
        sys.stderr.write(f"missing context file at {CONTEXT_PATH}\n")
        OUT_PATH.write_text(json.dumps({"error": "no context", "drift_items": []}))
        return

    try:
        ctx = json.loads(CONTEXT_PATH.read_text())
    except Exception as exc:
        OUT_PATH.write_text(json.dumps({"error": f"context parse failed: {exc}", "drift_items": []}))
        return

    if "error" in ctx:
        OUT_PATH.write_text(json.dumps({"error": f"upstream scanner error: {ctx['error']}", "drift_items": []}))
        return

    doc_text, doc_files = load_docs()

    # Code side
    c_cmds = code_commands(ctx)
    c_envs = code_envvars(ctx)
    c_ports = code_ports(ctx)
    c_dirs = code_dirs(ctx)

    # Doc side
    d_cmds = extract_doc_commands(doc_text)
    d_envs = extract_doc_envvars(doc_text)
    d_ports = extract_doc_ports(doc_text)
    d_dirs = extract_doc_dirs(doc_text)

    items: list[dict] = []
    next_id = 0

    def add(kind: str, value, explanation: str, severity: str) -> None:
        nonlocal next_id
        next_id += 1
        items.append({
            "id": f"DRIFT_{next_id:03d}",
            "kind": kind,
            "value": value,
            "explanation": explanation,
            "severity": severity,
        })

    # Stale commands: in docs but not in code
    # Skip common universal commands that aren't expected to be in package scripts.
    universal_cmds = {
        "npm install", "npm ci", "npm test", "npm run build", "npm run start",
        "yarn install", "yarn build", "pnpm install", "pnpm build",
        "cargo build", "cargo test", "cargo run", "cargo check", "cargo clippy",
        "cargo fmt", "cargo doc", "cargo update", "cargo tauri",
    }
    for cmd in sorted(d_cmds - c_cmds - universal_cmds):
        add("stale_command", cmd,
            "Documented command not found in any package.json scripts / Cargo bin / pyproject scripts",
            "high")

    # Missing commands: in code but not in docs (only flag *interesting* ones)
    interesting_prefixes = ("npm run dev", "npm run build", "npm run test", "npm run start",
                            "npm run lint", "npm run typecheck", "npm run db", "npm run k8s",
                            "npm run seed", "npm run deploy", "npm run migrate")
    for cmd in sorted(c_cmds - d_cmds):
        if any(cmd.startswith(p) for p in interesting_prefixes):
            add("missing_command", cmd,
                "Code defines this script but no doc mentions it",
                "medium")

    # Stale env vars
    # Filter: ignore generic token-like names that show up in examples
    boring_envs = {"NODE_ENV", "PATH", "HOME", "USER", "SHELL", "PWD"}
    for env in sorted((d_envs - c_envs) - boring_envs):
        add("stale_envvar", env,
            "Documented env var not referenced in any source file",
            "high")

    # Missing env vars (high signal — devs need to know about them)
    for env in sorted((c_envs - d_envs) - boring_envs):
        add("missing_envvar", env,
            "Source code reads this env var but no doc mentions it",
            "high")

    # Stale ports
    common_http_ports = {80, 443, 8080}
    for port in sorted(d_ports - c_ports - common_http_ports):
        add("stale_port", port,
            "Documented port not found anywhere in code or config",
            "high")

    # New ports
    for port in sorted(c_ports - d_ports - common_http_ports):
        add("missing_port", port,
            "Code uses this port but docs don't mention it",
            "medium")

    # Stale directories (in docs but not in repo)
    for dir_name in sorted(d_dirs - c_dirs):
        # Common confusables to skip: /api/, /v1/ etc that aren't real dirs
        if dir_name.startswith(("api", "v1", "v2", "graphql")):
            continue
        add("stale_dir", dir_name,
            "Documented top-level directory does not exist in repo",
            "medium")

    # Major undocumented dirs
    for dir_name in sorted(c_dirs - d_dirs):
        if dir_name in {"src", "lib", "tests", "test", ".github"}:
            continue
        # Get file count
        count = next((d.get("files", 0) for d in ctx.get("top_level_dirs", []) if d["name"] == dir_name), 0)
        if count >= 10:
            add("undocumented_dir", dir_name,
                f"Directory contains {count} files but no doc mentions it",
                "medium")

    # Counts
    stats: dict[str, int] = {}
    for it in items:
        stats[it["kind"]] = stats.get(it["kind"], 0) + 1
    stats["total"] = len(items)
    stats["high"] = sum(1 for it in items if it["severity"] == "high")
    stats["medium"] = sum(1 for it in items if it["severity"] == "medium")
    stats["low"] = sum(1 for it in items if it["severity"] == "low")

    report = {
        "doc_files_inspected": doc_files,
        "stats": stats,
        "drift_items": items,
        "code_summary": {
            "commands_count": len(c_cmds),
            "envvars_count": len(c_envs),
            "ports_count": len(c_ports),
            "top_dirs_count": len(c_dirs),
        },
        "doc_summary": {
            "commands_count": len(d_cmds),
            "envvars_count": len(d_envs),
            "ports_count": len(d_ports),
            "top_dirs_count": len(d_dirs),
        },
    }

    OUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True))
    print(f"Drift report written to {OUT_PATH}: {stats['total']} items "
          f"({stats['high']} high, {stats['medium']} medium, {stats['low']} low)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"drift-detector failed: {exc}\n")
        OUT_PATH.write_text(json.dumps({"error": str(exc), "drift_items": []}))
        sys.exit(0)
