from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from arma3_wiki_mcp import server

from .conftest import COMMANDS, make_stale, write_wiki

SHA = "abc1234def5678"


def zip_of(tree: Path, top: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in tree.rglob("*"):
            zf.write(p, f"{top}/{p.relative_to(tree).as_posix()}")
    return buf.getvalue()


@pytest.fixture
def fake_github(tmp_path, monkeypatch):
    """Serve a zip of a fake wiki in place of GitHub, with git 'not installed'."""
    tree = write_wiki(tmp_path / "upstream")
    calls = []

    def fake_http(url: str) -> bytes:
        calls.append(url)
        if "api.github.com" in url:
            return json.dumps({"sha": SHA}).encode()
        return zip_of(tree, f"arma3-wiki-{SHA}")

    monkeypatch.setattr(server, "have_git", lambda: False)
    monkeypatch.setattr(server, "_http", fake_http)
    return calls


def test_zip_download_without_git(data_dir, fake_github):
    assert server.refresh() == "downloaded"
    assert (data_dir / ".revision").read_text() == SHA
    assert len(server._index["commands"]) == len(COMMANDS)
    assert server._status["revision"] == "abc1234 (zip download)"
    assert not list(data_dir.parent.glob(".arma3-wiki-*")), "temp folder left behind"


def test_zip_download_skips_when_sha_unchanged(data_dir, fake_github):
    server.refresh()
    assert server.refresh(force=True) == "already current"
    assert sum("codeload" in u for u in fake_github) == 1


def test_zip_download_replaces_old_data(data_dir, fake_github):
    server.refresh()
    (data_dir / ".revision").write_text("old")
    (data_dir / "commands" / "stale.yml").write_text("name: stale\n")
    assert server.refresh(force=True) == "downloaded"
    assert not (data_dir / "commands" / "stale.yml").exists()


def test_zip_download_refuses_to_overwrite_a_foreign_folder(data_dir, fake_github):
    data_dir.mkdir()
    (data_dir / "precious.txt").write_text("keep me")
    assert server.refresh().startswith("failed: ")
    assert (data_dir / "precious.txt").read_text() == "keep me"


def test_fresh_cache_is_not_refreshed(wiki, monkeypatch):
    monkeypatch.setattr(server, "_refresh_archive", lambda: pytest.fail("should not fetch"))
    monkeypatch.setattr(server, "have_git", lambda: False)
    assert server.refresh() == "skipped (refreshed recently)"


def test_failed_refresh_keeps_serving_cached_data(wiki, monkeypatch):
    def boom():
        raise OSError("network down")

    make_stale(wiki / ".last_refresh")
    monkeypatch.setattr(server, "have_git", lambda: False)
    monkeypatch.setattr(server, "_refresh_archive", boom)
    monkeypatch.setattr(server, "_index", {"commands": [], "events": []})
    assert server.refresh() == "failed: network down"
    assert server._status["last_error"] == "network down"
    assert "setDamage" in server.lookup_command("setDamage")


@pytest.mark.skipif(not server.have_git(), reason="git not installed")
def test_git_clone_then_update(tmp_path, data_dir, monkeypatch):
    upstream = tmp_path / "upstream"
    write_wiki(upstream)
    commit = ("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm")
    server.git("init", "-q", "-b", "dist", str(upstream))
    server.git("add", ".", cwd=upstream)
    server.git(*commit, "initial", cwd=upstream)
    monkeypatch.setattr(server, "UPSTREAM", upstream.as_uri())

    assert server.refresh() == "cloned"
    assert len(server._index["commands"]) == len(COMMANDS)
    assert server._status["revision"].endswith("initial")

    assert server.refresh(force=True) == "already current"

    (upstream / "commands" / "hint.yml").write_text("name: hint\ndescription: Shows a hint.\n")
    server.git("add", ".", cwd=upstream)
    server.git(*commit, "add hint", cwd=upstream)
    assert server.refresh(force=True) == "updated"
    assert "Shows a hint." in server.lookup_command("hint")
