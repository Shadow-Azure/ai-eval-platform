"""Shared local S3-ish stub for callback tests (M2-05).

Exercises the real HTTP verification path (presigned HEAD) without network
access to OSS/MinIO. The production round-trip is proven on the host smoke.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubObjectHandler(BaseHTTPRequestHandler):
    server_version = "StubS3/1"

    def do_HEAD(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        known = getattr(self.server, "known_objects", {})
        size = known.get(path)
        if size is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.end_headers()

    def log_message(self, *_args) -> None:
        return


class StubObjectStore:
    """Context manager around ThreadingHTTPServer with a mutable object map."""

    def __init__(self, bucket: str = "eval-artifacts") -> None:
        self.bucket = bucket
        self.known: dict[str, int] = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), StubObjectHandler)
        self._server.known_objects = self.known
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> StubObjectStore:
        self._thread.start()
        host, port = self._server.server_address[:2]
        self.endpoint = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._thread.join()

    def add(self, object_key: str, size: int) -> str:
        self.known[f"/{self.bucket}/{object_key}"] = size
        return object_key

    def has(self, object_key: str) -> bool:
        return f"/{self.bucket}/{object_key}" in self.known
