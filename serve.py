"""Serve the static site locally.

    python3 serve.py        ->  http://localhost:8137

Needed because the page uses fetch() to load its JSON, and browsers refuse
cross-origin fetches from file:// URLs. Any static server will do — this is
just the one with no install step.
"""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
from pathlib import Path

PORT = int(os.environ.get("PORT", "8137"))
SITE = Path(__file__).resolve().parent / "site"


def main() -> None:
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(SITE)
    )
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), handler) as httpd:
        print("serving {} at http://localhost:{}".format(SITE, PORT))
        httpd.serve_forever()


if __name__ == "__main__":
    main()
