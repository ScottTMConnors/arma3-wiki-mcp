from __future__ import annotations

import sys
from pathlib import Path

import pytest

from arma3_wiki_mcp import server


def test_field_reads_inline_and_block_values():
    text = "name: x\ndescription: |-\n  First line.\n  Second.\nother: >\n  folded\n"
    assert server._field("name: x\ndescription: Plain.\n", "description") == "Plain."
    assert server._field(text, "description") == "First line."
    assert server._field(text, "other") == "folded"
    assert server._field(text, "missing") == ""


def test_groups():
    assert server._groups("groups:\n- A\n- B C\nsyntax:\n") == ["A", "B C"]
    assert server._groups("name: x\n") == []


def test_index_counts(wiki):
    assert len(server._index["commands"]) == 4
    assert len(server._index["events"]) == 3


def test_lookup_command_is_case_insensitive(wiki):
    assert "effect_loc: Global" in server.lookup_command("SETDAMAGE")


def test_lookup_command_decodes_url_encoded_file_names(wiki):
    assert "negation" in server.lookup_command("!")


def test_lookup_command_suggests_near_matches(wiki):
    out = server.lookup_command("Damage")
    assert out.startswith("No exact match") and "setDamage" in out


def test_lookup_event_returns_every_category(wiki):
    out = server.lookup_event("killed")
    assert "# category: standard" in out and "# category: mission" in out


def test_lookup_event_filters_by_category(wiki):
    out = server.lookup_event("Killed", "mission")
    assert "Mission version" in out and "standard" not in out


def test_search_ranks_name_matches_first(wiki):
    lines = server.search("killed").splitlines()
    assert lines[0] == "3 matches"
    assert "Killed" in lines[1]


def test_search_requires_every_word_and_respects_kind_and_limit(wiki):
    assert server.search("multiplayer variable", kind="commands").startswith("1 matches")
    assert server.search("killed", kind="commands").startswith("0 matches")
    out = server.search("killed", limit=1)
    assert "(showing 1)" in out and len(out.splitlines()) == 2
    assert server.search("   ") == "Empty query."


def test_list_group(wiki):
    assert "- Multiplayer (2)" in server.list_group("").splitlines()
    out = server.list_group("multiplayer")
    assert "remoteExec: Asks the server" in out and "publicVariable" in out
    assert server.list_group("Nope") == "No commands in group 'Nope'."


def test_tutorials_ship_with_the_package():
    assert "- best-practices" in server.list_tutorials().splitlines()
    assert "Code Best Practices" in server.get_tutorial("best-practices")


def test_get_tutorial_cannot_escape_the_folder():
    assert server.get_tutorial("../server").startswith("Unknown tutorial")


def test_wiki_status(wiki):
    assert "commands: 4, events: 3" in server.wiki_status()


@pytest.mark.parametrize(
    ("platform", "env", "expected"),
    [
        ("win32", {"LOCALAPPDATA": "/la"}, Path("/la/arma3-wiki-mcp/wiki")),
        ("linux", {"XDG_CACHE_HOME": "/xdg"}, Path("/xdg/arma3-wiki-mcp/wiki")),
        ("linux", {}, Path.home() / ".cache/arma3-wiki-mcp/wiki"),
        ("darwin", {}, Path.home() / "Library/Caches/arma3-wiki-mcp/wiki"),
    ],
)
def test_default_data_dir(monkeypatch, platform, env, expected):
    monkeypatch.setattr(sys, "platform", platform)
    for key in ("LOCALAPPDATA", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert server._default_data_dir() == expected
