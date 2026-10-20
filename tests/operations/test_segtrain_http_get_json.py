"""ingest.http_get_json: OData GET with a socket timeout; network failures become NornirUserException."""

from __future__ import annotations

import io
from email.message import Message
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining import ingest

URL = "http://example/odata/Locations"


class _Response(io.BytesIO):
    """Context-manager response stub returning fixed bytes."""


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, behavior: Any) -> list[dict[str, Any]]:
    """Replace ``ingest.urlopen``; record each call's request and kwargs."""
    calls: list[dict[str, Any]] = []

    def fake_urlopen(request: Request, *args: Any, **kwargs: Any) -> Any:
        calls.append({"request": request, "args": args, "kwargs": kwargs})
        if isinstance(behavior, BaseException):
            raise behavior
        return _Response(behavior)

    monkeypatch.setattr(ingest, "urlopen", fake_urlopen)
    return calls


def test_success_returns_parsed_json_and_sends_accept_header(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_urlopen(monkeypatch, b'{"value": [1, 2]}')
    assert ingest.http_get_json(URL) == {"value": [1, 2]}
    request = calls[0]["request"]
    assert request.full_url == URL
    assert request.get_header("Accept") == "application/json"


def test_urlopen_is_given_a_finite_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_urlopen(monkeypatch, b"{}")
    ingest.http_get_json(URL)
    timeout = calls[0]["kwargs"].get("timeout")
    assert timeout == ingest.ODATA_TIMEOUT_SECONDS
    assert 0 < timeout < float("inf")


def test_read_stall_timeout_becomes_user_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, TimeoutError("timed out"))
    with pytest.raises(NornirUserException) as info:
        ingest.http_get_json(URL)
    assert URL in str(info.value)
    assert "timed out" in str(info.value)
    assert isinstance(info.value.__cause__, TimeoutError)


def test_url_error_becomes_user_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, URLError("connection refused"))
    with pytest.raises(NornirUserException, match="OData request failed"):
        ingest.http_get_json(URL)


def test_http_error_body_is_included(monkeypatch: pytest.MonkeyPatch) -> None:
    error = HTTPError(URL, 400, "Bad Request", Message(), io.BytesIO(b"node count exceeded"))
    _patch_urlopen(monkeypatch, error)
    with pytest.raises(NornirUserException) as info:
        ingest.http_get_json(URL)
    assert "node count exceeded" in str(info.value)
    assert "400" in str(info.value)


def test_plain_value_error_is_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, ValueError("unknown url type"))
    with pytest.raises(ValueError, match="unknown url type"):
        ingest.http_get_json(URL)
