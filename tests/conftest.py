from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from arma3_wiki_mcp import server

# A tiny stand-in for the upstream `dist` branch, in the same layout and YAML shape.
COMMANDS = {
    "setDamage": """name: setDamage
description: Damages / repairs the object.
groups:
- Object Manipulation
syntax:
- call: obj setDamage value
argument_loc: Global
effect_loc: Global
examples:
- player setDamage 1;
""",
    "remoteExec": """name: remoteExec
description: |-
  Asks the server to execute the given function or command on the given target.
  Second line.
groups:
- Multiplayer
- Remote Execution
""",
    "%21": """name: '!'
description: Returns the negation of a Boolean expression.
groups:
- Variables
- Math
""",
    "publicVariable": """name: publicVariable
description: Broadcasts a missionNamespace variable to all clients.
groups:
- Multiplayer
- Variables
""",
}
EVENTS = {
    ("standard", "Killed"): "id: Killed\ndescription: Triggered when the unit is killed.\n",
    ("mission", "Killed"): "id: Killed\ndescription: Mission version of Killed.\n",
    ("multiplayer", "MPKilled"): "id: MPKilled\ndescription: Triggered when the unit is killed, on every machine.\n",
}


def write_wiki(root: Path) -> Path:
    (root / "commands").mkdir(parents=True, exist_ok=True)
    for name, text in COMMANDS.items():
        (root / "commands" / f"{name}.yml").write_text(text, encoding="utf-8")
    for (category, name), text in EVENTS.items():
        (root / "events" / category).mkdir(parents=True, exist_ok=True)
        (root / "events" / category / f"{name}.yml").write_text(text, encoding="utf-8")
    return root


def make_stale(stamp: Path) -> None:
    old = time.time() - 10 * 24 * 3600
    os.utime(stamp, (old, old))


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty cache folder the server is pointed at."""
    data = tmp_path / "wiki"
    monkeypatch.setattr(server, "DATA", data)
    monkeypatch.setattr(server, "STAMP", data / ".last_refresh")
    monkeypatch.setattr(server, "REVISION_FILE", data / ".revision")
    monkeypatch.setattr(server, "_index", {"commands": [], "events": []})
    monkeypatch.setattr(server, "_status", {"last_refresh": None, "last_error": None, "revision": None})
    return data


@pytest.fixture
def wiki(data_dir: Path) -> Path:
    """Populated, freshly refreshed cache with the index built."""
    write_wiki(data_dir)
    (data_dir / ".last_refresh").touch()
    server.build_index()
    return data_dir
