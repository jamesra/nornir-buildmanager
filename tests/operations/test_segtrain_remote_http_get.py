"""Characterization tests for ``remote_tiles.default_http_get``.

Runs against a loopback ``http.server`` on an ephemeral port. ``time.sleep`` is patched so the retry
backoff does not slow the suite.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

from nornir_buildmanager.operations.segmentationtraining import remote_tiles
from nornir_buildmanager.operations.segmentationtraining.remote_tiles import (
    default_http_get,
)


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves ``server.routes[path]`` as a list of ``(status, headers, body)``, one per request."""

    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        server = self.server
        server.hits[self.path] = server.hits.get(self.path, 0) + 1  # type: ignore[attr-defined]
        script = server.routes[self.path]  # type: ignore[attr-defined]
        status, headers, body = script[min(server.hits[self.path], len(script)) - 1]  # type: ignore[attr-defined]
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> Iterator[http.server.ThreadingHTTPServer]:
    monkeypatch.setattr(remote_tiles.time, "sleep", lambda _seconds: None)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.routes = {}  # type: ignore[attr-defined]
    httpd.hits = {}  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        remote_tiles._local.__dict__.pop("pool", None)


def _url(httpd: http.server.ThreadingHTTPServer, path: str) -> str:
    return f"http://127.0.0.1:{httpd.server_address[1]}{path}"


def test_ok_returns_body_and_lowercased_headers(server) -> None:
    server.routes["/a"] = [(200, {"X-Custom": "Yes"}, b"payload")]
    response = default_http_get(_url(server, "/a"), None)
    assert response.status == 200
    assert response.body == b"payload"
    assert response.headers["x-custom"] == "Yes"
    assert all(key == key.lower() for key in response.headers)


def test_extra_headers_are_sent(server) -> None:
    seen: dict[str, str] = {}

    class _Echo(_Handler):
        def do_GET(self) -> None:
            seen["range"] = self.headers.get("Range", "")
            seen["accept"] = self.headers.get("Accept", "")
            super().do_GET()

    server.RequestHandlerClass = _Echo
    server.routes["/h"] = [(200, {}, b"x")]
    default_http_get(_url(server, "/h"), {"Range": "bytes=0-1"})
    assert seen == {"range": "bytes=0-1", "accept": "*/*"}


@pytest.mark.parametrize("status", [304, 404])
def test_client_errors_and_not_modified_are_returned_not_raised(server, status: int) -> None:
    server.routes["/c"] = [(status, {}, b"")]
    response = default_http_get(_url(server, "/c"), None)
    assert response.status == status
    assert server.hits["/c"] == 1


@pytest.mark.parametrize("status", [301, 302])
def test_relative_redirect_is_followed(server, status: int) -> None:
    server.routes["/old"] = [(status, {"Location": "/new/place?q=1"}, b"")]
    server.routes["/new/place?q=1"] = [(200, {}, b"landed")]
    response = default_http_get(_url(server, "/old"), None)
    assert response.status == 200
    assert response.body == b"landed"


def test_redirect_loop_raises_too_many_redirects(server) -> None:
    server.routes["/loop"] = [(302, {"Location": "/loop"}, b"")]
    with pytest.raises(OSError, match="Too many redirects"):
        default_http_get(_url(server, "/loop"), None)
    assert server.hits["/loop"] == remote_tiles._MAX_REDIRECTS + 1


def test_redirect_without_location_is_returned(server) -> None:
    server.routes["/nl"] = [(302, {}, b"")]
    assert default_http_get(_url(server, "/nl"), None).status == 302


def test_transient_5xx_is_retried(server) -> None:
    server.routes["/flaky"] = [(503, {}, b""), (500, {}, b""), (200, {}, b"ok")]
    response = default_http_get(_url(server, "/flaky"), None)
    assert response.status == 200
    assert response.body == b"ok"
    assert server.hits["/flaky"] == 3


def test_persistent_5xx_raises_after_retries(server) -> None:
    server.routes["/down"] = [(503, {}, b"")]
    with pytest.raises(OSError, match="failed"):
        default_http_get(_url(server, "/down"), None)
    assert server.hits["/down"] == remote_tiles._RETRIES


@pytest.mark.parametrize("url", ["ftp://127.0.0.1/x", "http:///path", "/just/a/path", "file:///etc/hosts"])
def test_non_http_urls_are_rejected(url: str) -> None:
    with pytest.raises(OSError, match="Not an http"):
        default_http_get(url, None)
