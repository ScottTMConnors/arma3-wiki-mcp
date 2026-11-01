#!/usr/bin/env python3
"""Run the server from a source checkout: `python server.py`.

Kept so MCP client configs that point at this file keep working. If the checkout
has a `wiki/` folder from an older version, it is reused instead of downloading
the data again.
"""

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
if not os.environ.get("ARMA3_WIKI_DATA") and (HERE / "wiki" / "commands").is_dir():
    os.environ["ARMA3_WIKI_DATA"] = str(HERE / "wiki")

from arma3_wiki_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
