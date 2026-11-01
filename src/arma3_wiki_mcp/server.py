"""MCP server for the Arma 3 wiki data published on acemod/arma3-wiki's `dist` branch.

Keeps a copy of that branch in a per-user cache folder (or ARMA3_WIKI_DATA) and refreshes it on startup
and every ARMA3_WIKI_REFRESH_HOURS (default 24). Uses a shallow git clone when git is
installed, otherwise downloads a zip archive from GitHub. Exposes command, event,
search and tutorial lookups over stdio.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import __version__


def _default_data_dir() -> Path:
    """Per-user cache folder; the installed package itself may be read-only."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "arma3-wiki-mcp" / "wiki"


HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("ARMA3_WIKI_DATA") or _default_data_dir())
TUTORIALS = HERE / "tutorials"
UPSTREAM = os.environ.get("ARMA3_WIKI_UPSTREAM", "https://github.com/acemod/arma3-wiki.git")
BRANCH = os.environ.get("ARMA3_WIKI_BRANCH", "dist")
REFRESH_SECONDS = float(os.environ.get("ARMA3_WIKI_REFRESH_HOURS", "24")) * 3600
STAMP = DATA / ".last_refresh"
REVISION_FILE = DATA / ".revision"  # written in zip-download mode
GITHUB = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", UPSTREAM)
GIT_TIMEOUT = 600
SEARCH_LIMIT = 25

mcp = FastMCP("arma3-wiki")
_lock = threading.RLock()
_index: dict[str, list[dict]] = {"commands": [], "events": []}
_status = {"last_refresh": None, "last_error": None, "revision": None}


def log(msg: str) -> None:
    print(f"[arma3-wiki-mcp] {msg}", file=sys.stderr, flush=True)


def have_git() -> bool:
    return shutil.which("git") is not None


def git(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},  # never wait for a credential prompt
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no console flash on Windows
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def has_data() -> bool:
    return (DATA / "commands").is_dir()


def _stale() -> bool:
    return not STAMP.exists() or time.time() - STAMP.stat().st_mtime >= REFRESH_SECONDS


def _http(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "arma3-wiki-mcp"})
    with urllib.request.urlopen(req, timeout=GIT_TIMEOUT) as resp:
        return resp.read()


def _rmtree(path: Path) -> None:
    def make_writable(func, p, _exc):  # git pack files are read-only on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=make_writable)
    else:
        shutil.rmtree(path, onerror=make_writable)


def _refresh_archive() -> str:
    """Download the branch as a zip from GitHub; used when git is not installed."""
    if not GITHUB:
        raise RuntimeError("git is not installed and ARMA3_WIKI_UPSTREAM is not a GitHub URL; install git")
    if DATA.exists() and any(DATA.iterdir()) and not (REVISION_FILE.exists() or (DATA / ".git").exists()):
        raise RuntimeError(f"{DATA} is not empty and was not created by this server; refusing to replace it")
    owner, repo = GITHUB.groups()
    sha = json.loads(_http(f"https://api.github.com/repos/{owner}/{repo}/commits/{urllib.parse.quote(BRANCH)}"))["sha"]
    if has_data() and REVISION_FILE.exists() and REVISION_FILE.read_text().strip() == sha:
        return "already current"
    log(f"downloading {owner}/{repo}@{sha[:7]} ({BRANCH}) into {DATA}")
    archive = _http(f"https://codeload.github.com/{owner}/{repo}/zip/{sha}")
    DATA.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".arma3-wiki-", dir=DATA.parent))
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            zf.extractall(tmp)
        root = next(p for p in tmp.iterdir() if p.is_dir())
        (root / REVISION_FILE.name).write_text(sha)
        if DATA.exists():
            _rmtree(DATA)
        root.rename(DATA)
    finally:
        _rmtree(tmp)
    return "downloaded"


def _revision() -> str | None:
    if (DATA / ".git").exists() and have_git():
        return git("log", "-1", "--format=%h %cs %s", cwd=DATA)
    if REVISION_FILE.exists():
        return REVISION_FILE.read_text().strip()[:7] + " (zip download)"
    return None


def refresh(force: bool = False) -> str:
    """Clone or fast-update the data cache; this directory is owned by the server."""
    with _lock:
        try:
            if has_data() and not force and not _stale():
                result = "skipped (refreshed recently)"
            elif have_git() and (DATA / ".git").exists():
                before = git("rev-parse", "HEAD", cwd=DATA)
                git("fetch", "--depth", "1", "origin", BRANCH, cwd=DATA)
                git("reset", "--hard", "FETCH_HEAD", cwd=DATA)
                after = git("rev-parse", "HEAD", cwd=DATA)
                result = "updated" if before != after else "already current"
            elif have_git() and (not DATA.exists() or not any(DATA.iterdir())):
                log(f"cloning {UPSTREAM} ({BRANCH}) into {DATA}")
                git("clone", "--depth", "1", "--branch", BRANCH, "--single-branch", UPSTREAM, str(DATA))
                result = "cloned"
            else:
                result = _refresh_archive()
            STAMP.touch()
            _status["revision"] = _revision()
            _status["last_refresh"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _status["last_error"] = None
            build_index()
            log(f"refresh: {result}; {_status['revision']}")
            return result
        except Exception as exc:  # keep serving cached data
            _status["last_error"] = str(exc)
            log(f"refresh failed: {exc}")
            if not _index["commands"] and has_data():
                build_index()
            return f"failed: {exc}"


def _field(text: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(\|-?|>-?)?\s*(.*)$", text, re.M)
    if not m:
        return ""
    if m.group(2):
        return m.group(2).strip()
    block = re.search(rf"^{key}:.*\n((?:[ \t]+.*\n?)+)", text, re.M)
    return block.group(1).strip().splitlines()[0].strip() if block else ""


def _groups(text: str) -> list[str]:
    m = re.search(r"^groups:\s*\n((?:- .*\n?)+)", text, re.M)
    return [g[2:].strip() for g in m.group(1).splitlines()] if m else []


def _entry(path: Path, kind: str) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    name = urllib.parse.unquote(path.stem)
    entry = {"name": name, "path": path, "summary": _field(text, "description")[:200], "groups": _groups(text)}
    if kind == "events":
        entry["category"] = path.parent.name
    entry["haystack"] = (name + " " + " ".join(entry["groups"]) + " " + text).lower()
    return entry


def build_index() -> None:
    commands = [_entry(p, "commands") for p in sorted((DATA / "commands").glob("*.yml"))]
    events = [_entry(p, "events") for p in sorted((DATA / "events").glob("*/*.yml"))]
    with _lock:
        _index["commands"], _index["events"] = commands, events
    log(f"indexed {len(commands)} commands, {len(events)} events")


def _find(kind: str, name: str, category: str | None = None) -> list[dict]:
    key = name.strip().lower()
    hits = [e for e in _index[kind] if e["name"].lower() == key]
    if category:
        hits = [e for e in hits if e.get("category", "").lower() == category.lower()]
    return hits


def _suggest(kind: str, name: str) -> str:
    key = name.strip().lower()
    near = [e["name"] for e in _index[kind] if key in e["name"].lower()][:15]
    return f"No exact match for '{name}'." + (f" Similar: {', '.join(near)}" if near else "")


@mcp.tool()
def lookup_command(name: str) -> str:
    """Full wiki YAML for an SQF script command (syntax, params, return, locality, examples). Case-insensitive."""
    hits = _find("commands", name)
    if not hits:
        return _suggest("commands", name)
    return hits[0]["path"].read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def lookup_event(name: str, category: str | None = None) -> str:
    """Full wiki YAML for an event handler. Categories: standard, eden, group, mission,
    multiplayer, music, projectile, user_action, user_interface."""
    hits = _find("events", name, category)
    if not hits:
        return _suggest("events", name)
    return "\n---\n".join(f"# category: {h['category']}\n" + h["path"].read_text(encoding="utf-8", errors="replace") for h in hits)


@mcp.tool()
def search(query: str, kind: str = "all", limit: int = SEARCH_LIMIT) -> str:
    """Keyword search over command/event names, groups and full text. All words must match.
    kind: all | commands | events. Name matches rank first."""
    words = query.lower().split()
    if not words:
        return "Empty query."
    kinds = ["commands", "events"] if kind == "all" else [kind]
    scored = []
    for k in kinds:
        for e in _index.get(k, []):
            if all(w in e["haystack"] for w in words):
                score = sum(3 for w in words if w in e["name"].lower()) + sum(1 for w in words if any(w in g.lower() for g in e["groups"]))
                scored.append((-score, k, e))
    scored.sort(key=lambda s: (s[0], s[2]["name"].lower()))
    lines = []
    for _, k, e in scored[: max(1, limit)]:
        label = f"event {e['category']}/{e['name']}" if k == "events" else f"command {e['name']}"
        lines.append(f"- {label} [{', '.join(e['groups'])}]: {e['summary']}")
    return f"{len(scored)} matches" + (f" (showing {len(lines)})" if len(scored) > len(lines) else "") + "\n" + "\n".join(lines)


@mcp.tool()
def list_group(group: str) -> str:
    """List commands in a wiki group (e.g. 'Multiplayer', 'Particles'). Pass '' to list all groups."""
    if not group.strip():
        groups: dict[str, int] = {}
        for e in _index["commands"]:
            for g in e["groups"]:
                groups[g] = groups.get(g, 0) + 1
        return "\n".join(f"- {g} ({n})" for g, n in sorted(groups.items()))
    hits = [e for e in _index["commands"] if any(g.lower() == group.lower() for g in e["groups"])]
    return "\n".join(f"- {e['name']}: {e['summary']}" for e in hits) or f"No commands in group '{group}'."


@mcp.tool()
def list_tutorials() -> str:
    """List the bundled scripting tutorials (best practices, MP scripting, optimisation)."""
    return "\n".join(f"- {p.stem}" for p in sorted(TUTORIALS.glob("*.md")))


@mcp.tool()
def get_tutorial(name: str) -> str:
    """Read a scripting tutorial by name (see list_tutorials)."""
    path = TUTORIALS / f"{Path(name).stem}.md"
    if not path.is_file():
        return f"Unknown tutorial '{name}'. Available: " + ", ".join(p.stem for p in TUTORIALS.glob("*.md"))
    return path.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
def wiki_status() -> str:
    """Data revision, cache location, entry counts and last refresh result."""
    return (f"data: {DATA}\nupstream: {UPSTREAM} ({BRANCH})\nrevision: {_status['revision']}\n"
            f"last refresh: {_status['last_refresh']}\nlast error: {_status['last_error']}\n"
            f"commands: {len(_index['commands'])}, events: {len(_index['events'])}\n"
            f"refresh interval: {REFRESH_SECONDS / 3600:g} h")


@mcp.tool()
def refresh_wiki() -> str:
    """Fetch the latest upstream wiki data now and rebuild the index."""
    return refresh(force=True)


def _refresher() -> None:
    while True:
        time.sleep(max(60.0, REFRESH_SECONDS))
        refresh(force=True)


def main() -> None:
    if any(a in ("--version", "-V") for a in sys.argv[1:]):
        print(f"arma3-wiki-mcp {__version__}")
        return
    if has_data():
        build_index()  # serve cached data immediately; refresh in background
        threading.Thread(target=refresh, daemon=True).start()
    else:
        refresh()
    threading.Thread(target=_refresher, daemon=True).start()
    mcp.run()


if __name__ == "__main__":
    main()
