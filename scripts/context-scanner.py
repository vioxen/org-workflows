#!/usr/bin/env python3
"""
Deterministic repo pre-scan for docs-refresh workflow.

Emits a JSON report on stdout describing the repo's *full surface*:
- All package files (recursive)
- HTTP routes
- Vue/Nuxt pages
- Rust workspace structure
- Env vars (extensive grep)
- Port literals
- Docker compose services
- gRPC service definitions
- Scheduled tasks
- Dockerfiles, CI workflows, top-level dirs

The Claude refresher reads this *and* the drift report (drift-detector.py
output) to find every important difference between docs and code.

No third-party dependencies. Python 3.10+.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd())).resolve()

IGNORE_DIRS = {
    ".git", "node_modules", "target", "dist", "build", ".next", ".nuxt",
    "__pycache__", ".venv", "venv", ".tox", ".pytest_cache", "coverage",
    ".cache", ".turbo", ".svelte-kit", ".output", "vendor", ".gradle",
    ".idea", ".vscode", ".DS_Store",
}

# File-count caps are intentionally generous: we want full coverage on
# any vioxen repo (largest known: governors-daemons ~1500 files).
MAX_FILES_SCANNED = 5000
MAX_PACKAGE_FILES = 100

PACKAGE_FILES = {
    "package.json": "node",
    "Cargo.toml": "rust",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "go.mod": "go",
    "composer.json": "php",
    "Gemfile": "ruby",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "kotlin",
    "mix.exs": "elixir",
    "deno.json": "deno",
    "bun.lockb": "bun",
}

CODE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go", ".rb", ".java", ".kt", ".vue")

DOC_FILES = ("README.md", "README.rst", "CLAUDE.md", "CHANGELOG.md", "CONTRIBUTING.md")


# ---------- helpers ----------

def run(cmd: list[str], cwd: Path = REPO_ROOT) -> str:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=15, check=False)
        return out.stdout.strip()
    except Exception as exc:
        return f"<error: {exc}>"


def walk_repo(extensions: tuple[str, ...] | None = None, max_files: int = MAX_FILES_SCANNED):
    """Yield (path, rel_path) for every file under repo, capped, ignoring noise dirs."""
    seen = 0
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if extensions and not f.endswith(extensions):
                continue
            seen += 1
            if seen > max_files:
                return
            full = Path(root) / f
            yield full, full.relative_to(REPO_ROOT)


def read_text(path: Path, max_bytes: int = 500_000) -> str | None:
    try:
        data = path.read_bytes()
    except Exception:
        return None
    if len(data) > max_bytes:
        return None
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return None


def _toml_field(content: str, key: str) -> str | None:
    m = re.search(rf'^\s*{re.escape(key)}\s*=\s*"([^"]+)"', content, flags=re.M)
    return m.group(1) if m else None


# ---------- detectors ----------

def detect_languages() -> dict[str, int]:
    counts: dict[str, int] = {}
    for full, _ in walk_repo(max_files=50_000):
        ext = full.suffix.lower().lstrip(".")
        if not ext or len(ext) > 8:
            continue
        counts[ext] = counts.get(ext, 0) + 1
    return counts


def detect_package_files() -> list[dict]:
    """All package manifest files anywhere in repo (capped)."""
    found: list[dict] = []
    seen = 0
    for full, rel in walk_repo():
        if seen >= MAX_PACKAGE_FILES:
            break
        if full.name in PACKAGE_FILES:
            found.append(_summarize_package_file(full))
            seen += 1
    return found


def _summarize_package_file(path: Path) -> dict:
    info: dict = {"path": str(path.relative_to(REPO_ROOT)), "ecosystem": PACKAGE_FILES.get(path.name, "unknown")}
    content = read_text(path)
    if content is None:
        info["error"] = "unreadable"
        return info

    if path.name == "package.json":
        try:
            data = json.loads(content)
            info["name"] = data.get("name")
            info["version"] = data.get("version")
            info["scripts"] = data.get("scripts") or {}  # full dict, not just keys
            info["dependencies"] = data.get("dependencies") or {}
            info["dev_dependencies"] = data.get("devDependencies") or {}
            info["peer_dependencies"] = data.get("peerDependencies") or {}
            info["workspaces"] = data.get("workspaces") or []
            info["engines"] = data.get("engines")
            info["bin"] = data.get("bin")
            info["main"] = data.get("main")
            info["type"] = data.get("type")
        except Exception as exc:
            info["error"] = f"package.json parse: {exc}"
    elif path.name == "Cargo.toml":
        info["name"] = _toml_field(content, "name")
        info["version"] = _toml_field(content, "version")
        info["edition"] = _toml_field(content, "edition")
        info["binaries"] = re.findall(r'^\s*\[\[bin\]\][\s\S]*?name\s*=\s*"([^"]+)"', content, flags=re.M)
        # Workspace members
        m = re.search(r'\[workspace\][\s\S]*?members\s*=\s*\[([\s\S]*?)\]', content, flags=re.M)
        if m:
            info["workspace_members"] = re.findall(r'"([^"]+)"', m.group(1))
        # Dependencies (top-level [dependencies] section, names only — versions are noisy)
        deps_match = re.search(r'^\[dependencies\]([\s\S]*?)(?=\n\[|\Z)', content, flags=re.M)
        if deps_match:
            info["dependencies"] = re.findall(r'^([a-zA-Z0-9_-]+)\s*=', deps_match.group(1), flags=re.M)
    elif path.name == "pyproject.toml":
        info["name"] = _toml_field(content, "name")
        info["version"] = _toml_field(content, "version")
        info["python_requires"] = _toml_field(content, "requires-python")
        # poetry / hatch / pdm scripts
        scripts_match = re.search(r'\[(?:tool\.poetry\.scripts|project\.scripts)\]([\s\S]*?)(?=\n\[|\Z)', content)
        if scripts_match:
            info["scripts"] = re.findall(r'^([a-zA-Z0-9_-]+)\s*=', scripts_match.group(1), flags=re.M)
    elif path.name == "go.mod":
        m = re.search(r"^module\s+(\S+)", content, flags=re.M)
        info["module"] = m.group(1) if m else None
        m = re.search(r"^go\s+(\S+)", content, flags=re.M)
        info["go_version"] = m.group(1) if m else None
    elif path.name == "requirements.txt":
        info["packages"] = [
            line.split("==")[0].split(">=")[0].split("~=")[0].strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ][:80]
    return info


def detect_routes() -> list[dict]:
    """Express / Fastify / NestJS / Koa-style HTTP routes."""
    routes: list[dict] = []
    patterns = [
        # app.get('/path'), router.post('/path')
        re.compile(rb'\b(?:app|router|api|server|fastify)\s*\.\s*(get|post|put|delete|patch|head|options|all|use)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', re.I),
        # NestJS: @Get('/path'), @Post('/path')
        re.compile(rb'@(Get|Post|Put|Delete|Patch|Head|Options|All)\s*\(\s*[\'"`]([^\'"`]*)[\'"`]?\s*\)', re.I),
    ]
    for full, rel in walk_repo(extensions=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        try:
            data = full.read_bytes()
        except Exception:
            continue
        if len(data) > 300_000:
            continue
        for pat in patterns:
            for m in pat.finditer(data):
                method = m.group(1).decode("ascii", errors="replace").upper()
                path = m.group(2).decode("utf-8", errors="replace")
                routes.append({"method": method, "path": path, "file": str(rel)})
                if len(routes) >= 500:
                    return routes
    return routes


def detect_vue_pages() -> list[str]:
    """Nuxt/Vue page files (file-system based routing)."""
    pages: list[str] = []
    for candidate in (REPO_ROOT / "pages", REPO_ROOT / "src" / "pages", REPO_ROOT / "app" / "pages"):
        if not candidate.is_dir():
            continue
        for full, _ in walk_repo(extensions=(".vue",)):
            if str(full).startswith(str(candidate)):
                pages.append(str(full.relative_to(REPO_ROOT)))
        if pages:
            break
    return pages[:200]


def detect_react_routes() -> list[dict]:
    """React Router v6 routes (heuristic)."""
    out: list[dict] = []
    pat = re.compile(rb'<Route\s+[^>]*path\s*=\s*[\'"]([^\'"]+)[\'"][^>]*element\s*=\s*\{<(\w+)', re.I)
    pat2 = re.compile(rb'<Route\s+[^>]*path\s*=\s*[\'"]([^\'"]+)[\'"]', re.I)
    for full, rel in walk_repo(extensions=(".tsx", ".jsx")):
        try:
            data = full.read_bytes()
        except Exception:
            continue
        if len(data) > 300_000 or b"<Route" not in data:
            continue
        for m in pat.finditer(data):
            out.append({"path": m.group(1).decode("utf-8", errors="replace"), "component": m.group(2).decode("ascii"), "file": str(rel)})
        if not any(r["file"] == str(rel) for r in out):
            for m in pat2.finditer(data):
                out.append({"path": m.group(1).decode("utf-8", errors="replace"), "file": str(rel)})
        if len(out) >= 200:
            return out
    return out


def detect_envvars() -> list[str]:
    """Extensive env var grep across all code files."""
    patterns = [re.compile(p) for p in (
        rb"process\.env\.([A-Z][A-Z0-9_]+)",
        rb"process\.env\[[\"']([A-Z][A-Z0-9_]+)[\"']\]",
        rb"std::env::var\([\"']([A-Z][A-Z0-9_]+)[\"']\)",
        rb"env::var\([\"']([A-Z][A-Z0-9_]+)[\"']\)",
        rb"os\.getenv\([\"']([A-Z][A-Z0-9_]+)[\"']\)",
        rb"os\.environ\.get\([\"']([A-Z][A-Z0-9_]+)[\"']\)",
        rb"os\.environ\[[\"']([A-Z][A-Z0-9_]+)[\"']\]",
        rb"ENV\[[\"']([A-Z][A-Z0-9_]+)[\"']\]",
        rb"runtimeConfig\.([a-z][a-zA-Z0-9_]+)",
    )]
    found: set[str] = set()
    for full, _ in walk_repo(extensions=CODE_EXTS, max_files=3000):
        try:
            data = full.read_bytes()
        except Exception:
            continue
        if len(data) > 300_000:
            continue
        for pat in patterns:
            for m in pat.finditer(data):
                found.add(m.group(1).decode("ascii", errors="replace"))
    return sorted(found)


def detect_ports() -> list[int]:
    """Port literals: explicit port numbers in code/config."""
    found: set[int] = set()
    patterns = [
        re.compile(rb'\b(?:port|PORT)\s*[=:]\s*[\'"]?(\d{2,5})\b'),
        re.compile(rb'\.listen\s*\(\s*(\d{2,5})\b'),
        re.compile(rb':(\d{4,5})(?:["\'/]|\s|$)'),  # :3000, :9100
    ]
    for full, _ in walk_repo(extensions=CODE_EXTS + (".yml", ".yaml", ".env", ".env.example", ".env.sample"), max_files=3000):
        try:
            data = full.read_bytes()
        except Exception:
            continue
        if len(data) > 300_000:
            continue
        for pat in patterns:
            for m in pat.finditer(data):
                try:
                    port = int(m.group(1))
                except ValueError:
                    continue
                if 1000 <= port <= 65535:  # ignore common false positives like :80, :443 in URLs
                    found.add(port)
    return sorted(found)


def detect_docker_services() -> list[str]:
    """Top-level service names from any docker-compose.yml."""
    services: set[str] = set()
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        path = REPO_ROOT / name
        if not path.exists():
            continue
        content = read_text(path)
        if not content:
            continue
        # Naive section-aware regex (don't pull in PyYAML unnecessarily)
        in_services = False
        for line in content.splitlines():
            stripped = line.rstrip()
            if stripped.startswith("services:"):
                in_services = True
                continue
            if in_services and stripped and not stripped.startswith(" ") and not stripped.startswith("\t") and ":" in stripped:
                # Left services block
                in_services = False
            if in_services and re.match(r"^  [a-zA-Z][a-zA-Z0-9_-]*:\s*$", line):
                services.add(line.strip().rstrip(":"))
    return sorted(services)


def detect_proto_files() -> list[dict]:
    """gRPC service definitions in .proto files."""
    out: list[dict] = []
    for full, rel in walk_repo(extensions=(".proto",)):
        content = read_text(full)
        if not content:
            continue
        services = re.findall(r'^\s*service\s+(\w+)\s*\{', content, flags=re.M)
        rpcs = re.findall(r'^\s*rpc\s+(\w+)\s*\(', content, flags=re.M)
        out.append({"file": str(rel), "services": services, "rpcs": rpcs})
        if len(out) >= 50:
            return out
    return out


def detect_scheduled_tasks() -> list[dict]:
    """Cron / setInterval / setTimeout-with-recurrence patterns."""
    out: list[dict] = []
    patterns = [
        (re.compile(rb"setInterval\s*\(\s*\w+\s*,\s*(\d+)"), "setInterval"),
        (re.compile(rb'@Cron\s*\(\s*[\'"]([^\'"]+)[\'"]'), "Cron"),
        (re.compile(rb'cron[:=]\s*[\'"]([^\'"]+)[\'"]'), "cron-config"),
        (re.compile(rb'schedule[:=]\s*[\'"]([^\'"]+)[\'"]'), "schedule"),
    ]
    for full, rel in walk_repo(extensions=CODE_EXTS + (".yml", ".yaml"), max_files=3000):
        try:
            data = full.read_bytes()
        except Exception:
            continue
        if len(data) > 300_000:
            continue
        for pat, label in patterns:
            for m in pat.finditer(data):
                out.append({"kind": label, "value": m.group(1).decode("utf-8", errors="replace"), "file": str(rel)})
                if len(out) >= 100:
                    return out
    return out


def detect_docs() -> list[str]:
    found: list[str] = []
    for f in DOC_FILES:
        if (REPO_ROOT / f).exists():
            found.append(f)
    if (REPO_ROOT / ".claude" / "CLAUDE.md").exists():
        found.append(".claude/CLAUDE.md")
    if (REPO_ROOT / "docs").is_dir():
        found.append("docs/")
    return found


def top_level_dirs() -> list[dict]:
    out: list[dict] = []
    for entry in sorted(REPO_ROOT.iterdir()):
        if entry.is_dir() and entry.name not in IGNORE_DIRS and not entry.name.startswith("."):
            file_count = 0
            for _, dirs, files in os.walk(entry):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
                file_count += len(files)
                if file_count > 5000:
                    break
            out.append({"name": entry.name, "files": file_count})
    return out


def detect_ci_files() -> list[str]:
    wf_dir = REPO_ROOT / ".github" / "workflows"
    if not wf_dir.is_dir():
        return []
    return sorted(p.name for p in wf_dir.iterdir() if p.suffix in (".yml", ".yaml"))


def detect_dockerfiles() -> list[str]:
    out: list[str] = []
    for full, rel in walk_repo():
        if full.name == "Dockerfile" or full.name.startswith("Dockerfile."):
            out.append(str(rel))
            if len(out) >= 30:
                return out
    return out


def detect_helm_charts() -> list[str]:
    out: list[str] = []
    for full, rel in walk_repo():
        if full.name == "Chart.yaml":
            out.append(str(rel))
            if len(out) >= 30:
                return out
    return out


# ---------- main ----------

def main() -> None:
    report = {
        "repo_root": str(REPO_ROOT),
        "default_branch": run(["git", "symbolic-ref", "--short", "HEAD"]) or "(unknown)",
        "head_sha": run(["git", "rev-parse", "HEAD"]),
        "remote_origin": run(["git", "remote", "get-url", "origin"]),
        "recent_commits": run(["git", "log", "--oneline", "-30"]).splitlines(),
        "top_level_dirs": top_level_dirs(),
        "languages": detect_languages(),
        "package_files": detect_package_files(),
        "existing_docs": detect_docs(),
        "ci_workflows": detect_ci_files(),
        "dockerfiles": detect_dockerfiles(),
        "helm_charts": detect_helm_charts(),
        "env_vars_referenced": detect_envvars(),
        "ports_referenced": detect_ports(),
        "http_routes": detect_routes(),
        "vue_pages": detect_vue_pages(),
        "react_routes": detect_react_routes(),
        "docker_services": detect_docker_services(),
        "proto_files": detect_proto_files(),
        "scheduled_tasks": detect_scheduled_tasks(),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"context-scanner failed: {exc}\n")
        json.dump({"error": str(exc)}, sys.stdout)
        sys.exit(0)
