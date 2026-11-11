"""Tiny sample product shipped by the example Jenkins pipeline."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        payload = {
            "product": os.environ.get("PRODUCT_NAME", "sample-product"),
            "status": "ok",
            "path": self.path,
        }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
