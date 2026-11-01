"""Render docs/demo.gif: an assistant answering an Arma 3 scripting question from this server.

The tool calls are run for real against the server's wiki cache, and the results shown
are taken from what they return. The answer text below is written from those results.

    pip install pillow
    python docs/demo/render_demo.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
if not os.environ.get("ARMA3_WIKI_DATA") and (ROOT / "wiki" / "commands").is_dir():
    os.environ["ARMA3_WIKI_DATA"] = str(ROOT / "wiki")

from arma3_wiki_mcp import server  # noqa: E402

OUT = ROOT / "docs" / "demo.gif"
W, H, PAD, LINE = 960, 450, 28, 24
BG, FG, DIM, ACCENT, GREEN, CODE_BG, BAR = "#1e1f24", "#e6e6e6", "#8b8f98", "#d98b5f", "#7fbf7f", "#2a2c33", "#2b2d33"
CODE_FG = "#c8d3f5"

QUESTION = "How do I show a hint to every player in a multiplayer mission?"
ANSWER = [
    ("text", "`hint` has a local effect (effect_loc: Local), so it only shows on the"),
    ("text", "machine that runs it. Broadcast it with `remoteExec` instead:"),
    ("gap", ""),
    ("code", '"Server restart in 5 minutes" remoteExec ["hint"];      // everyone'),
    ("code", '"Server restart in 5 minutes" remoteExec ["hint", -2];  // all but the server'),
    ("gap", ""),
    ("text", "Target 0 (the default) is fine for hints: per the wiki, a dedicated server"),
    ("text", "ignores `hint` anyway because it has no interface."),
]


def font(names: list[str], size: int) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


MONO = font(["consola.ttf", "DejaVuSansMono.ttf", "Menlo.ttc"], 16)
MONO_B = font(["consolab.ttf", "DejaVuSansMono-Bold.ttf", "Menlo.ttc"], 16)


def tool_calls() -> list[tuple[str, str]]:
    """Run the tools for real; return (call, one-line result) pairs."""
    server.build_index()
    if not server._index["commands"]:
        server.refresh(force=True)
    found = server.search("hint", kind="commands", limit=4).splitlines()
    names = ", ".join(re.match(r"- command (\S+)", line).group(1) for line in found[1:])
    hint = server.lookup_command("hint")
    effect = re.search(r"^effect_loc: .*$", hint, re.M).group(0)
    remote = server.lookup_command("remoteExec")
    example = re.search(r'\\"message\\" remoteExec \[\\"hint\\", -2\];', remote)
    assert example, "remoteExec example changed upstream; update the demo"
    return [
        ('search(query: "hint", kind: "commands")', f"{found[0].split(' (')[0]}: {names}, ..."),
        ('lookup_command(name: "hint")', effect),
        ('lookup_command(name: "remoteExec")', '"message" remoteExec ["hint", -2]; // everybody but the server'),
    ]


class Canvas:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []  # (kind, text)

    def frame(self, cursor: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 34], fill=BAR)
        for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
            d.ellipse([16 + i * 22, 11, 28 + i * 22, 23], fill=c)
        d.text((W // 2, 17), "assistant + arma3-wiki MCP", font=MONO, fill=DIM, anchor="mm")
        y = 34 + PAD
        for kind, text in self.rows:
            y = self.draw_row(d, kind, text, y)
        if cursor:
            d.rectangle([PAD, y + 2, PAD + 9, y + 20], fill=FG)
        return img

    def draw_row(self, d: ImageDraw.ImageDraw, kind: str, text: str, y: int) -> int:
        if kind == "prompt":
            d.text((PAD, y), "> ", font=MONO_B, fill=ACCENT)
            d.text((PAD + 20, y), text, font=MONO, fill=FG)
        elif kind in ("call", "call-run"):
            d.text((PAD, y), "●", font=MONO, fill=GREEN if kind == "call" else DIM)
            d.text((PAD + 20, y), "arma3-wiki", font=MONO_B, fill=FG)
            d.text((PAD + 20 + d.textlength("arma3-wiki ", font=MONO_B), y), text, font=MONO, fill=DIM)
        elif kind == "result":
            d.text((PAD + 20, y), "└ " + text, font=MONO, fill=DIM)
        elif kind == "code":
            d.rectangle([PAD + 8, y - 3, W - PAD, y + LINE - 1], fill=CODE_BG)
            d.text((PAD + 20, y), text, font=MONO, fill=CODE_FG)
        elif kind == "text":
            x = PAD
            for i, part in enumerate(text.split("`")):  # `code` spans in accent colour
                d.text((x, y), part, font=MONO, fill=ACCENT if i % 2 else FG)
                x += d.textlength(part, font=MONO)
        return y + (LINE // 2 if kind == "gap" else LINE)


def main() -> None:
    calls = tool_calls()
    c, frames, times = Canvas(), [], []

    def snap(ms: int, cursor: bool = False) -> None:
        frames.append(c.frame(cursor))
        times.append(ms)

    snap(700, cursor=True)
    c.rows.append(("prompt", ""))
    for i in range(0, len(QUESTION) + 1, 2):
        c.rows[-1] = ("prompt", QUESTION[:i])
        snap(45)
    c.rows[-1] = ("prompt", QUESTION)
    snap(700)
    c.rows.append(("gap", ""))
    for call, result in calls:
        c.rows.append(("call-run", call))
        snap(650)
        c.rows[-1] = ("call", call)
        c.rows.append(("result", result))
        snap(900)
    c.rows.append(("gap", ""))
    for kind, text in ANSWER:
        if kind == "text":
            words = text.split(" ")
            c.rows.append((kind, ""))
            for n in range(2, len(words) + 2, 2):
                c.rows[-1] = (kind, " ".join(words[:n]))
                snap(55)
        else:
            c.rows.append((kind, text))
            snap(250 if kind == "code" else 60)
    snap(5000)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=48) for f in frames]
    pal[0].save(OUT, save_all=True, append_images=pal[1:], duration=times, loop=0, optimize=True, disposal=1)
    print(f"wrote {OUT} ({len(frames)} frames, {OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
