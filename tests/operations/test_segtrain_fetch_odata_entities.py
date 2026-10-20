"""ingest._fetch_odata_entities: when the progress reporter is created, what it is told, and that it is completed."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from nornir_buildmanager.operations.segmentationtraining import ingest

URL = "http://example/odata/Locations"


class _RecordingReporter:
    """Stand-in for IterateProgressReporter that records every call in ``events``."""

    events: ClassVar[list[tuple[Any, ...]]] = []

    def __init__(self, track_id: str, total: int, *, label: str, depth: int = 0) -> None:
        self.events.append(("init", total, label, depth))

    def start(self) -> None:
        self.events.append(("start",))

    def update(self, current: int) -> None:
        self.events.append(("update", current))

    def complete(self) -> None:
        self.events.append(("complete",))


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Swap in the recording reporter and return its (fresh) event list."""
    recorded: list[tuple[Any, ...]] = []
    monkeypatch.setattr(_RecordingReporter, "events", recorded)
    monkeypatch.setattr(ingest, "IterateProgressReporter", _RecordingReporter)
    return recorded


def _entities(*ids: int) -> list[dict[str, Any]]:
    return [{"ID": i} for i in ids]


def _paged_getter(pages: dict[str, dict[str, Any]], seen: list[str] | None = None) -> Any:
    def getter(url: str) -> dict[str, Any]:
        if seen is not None:
            seen.append(url)
        return pages[url]

    return getter


def test_count_on_first_page_sets_total_and_reports_running_totals(events: list[tuple[Any, ...]]) -> None:
    next_url = "http://example/odata/Locations?$skip=2"
    pages = {
        URL: {"@odata.count": 3, "@odata.nextLink": next_url, "value": _entities(1, 2)},
        next_url: {"value": _entities(3)},
    }
    result = ingest._fetch_odata_entities(URL, _paged_getter(pages))
    assert result == _entities(1, 2, 3)
    assert events == [
        ("init", 3, ingest.INGEST_LABEL, 0),
        ("start",),
        ("update", 2),
        ("update", 3),
        ("complete",),
    ]


def test_next_link_without_count_and_empty_last_page_reports_collected_total(events: list[tuple[Any, ...]]) -> None:
    next_url = "http://example/odata/Locations?$skip=2"
    pages = {
        URL: {"@odata.nextLink": next_url, "value": _entities(1, 2)},
        next_url: {"value": []},
    }
    result = ingest._fetch_odata_entities(URL, _paged_getter(pages))
    assert result == _entities(1, 2)
    # No reporter exists while a nextLink is pending; the end-of-loop fallback sizes it by len(collected).
    assert events == [
        ("init", 2, ingest.INGEST_LABEL, 0),
        ("start",),
        ("update", 2),
        ("complete",),
    ]


def test_next_link_without_count_creates_reporter_on_final_page_sized_by_that_page(
    events: list[tuple[Any, ...]],
) -> None:
    """Pins current behavior: the final page's length becomes the total, so the bar can read 3/1."""
    next_url = "http://example/odata/Locations?$skip=2"
    pages = {
        URL: {"@odata.nextLink": next_url, "value": _entities(1, 2)},
        next_url: {"value": _entities(3)},
    }
    result = ingest._fetch_odata_entities(URL, _paged_getter(pages))
    assert result == _entities(1, 2, 3)
    assert events == [
        ("init", 1, ingest.INGEST_LABEL, 0),
        ("start",),
        ("update", 3),
        ("complete",),
    ]


def test_single_page_without_count_uses_page_length_as_total(events: list[tuple[Any, ...]]) -> None:
    result = ingest._fetch_odata_entities(URL, _paged_getter({URL: {"value": _entities(1, 2, 3, 4)}}))
    assert len(result) == 4
    assert events == [
        ("init", 4, ingest.INGEST_LABEL, 0),
        ("start",),
        ("update", 4),
        ("complete",),
    ]


def test_bare_list_document_is_a_single_page(events: list[tuple[Any, ...]]) -> None:
    result = ingest._fetch_odata_entities(URL, lambda url: _entities(7, 8))  # type: ignore[arg-type, return-value]
    assert result == _entities(7, 8)
    assert events[0] == ("init", 2, ingest.INGEST_LABEL, 0)
    assert events[-1] == ("complete",)


def test_empty_result_creates_no_reporter(events: list[tuple[Any, ...]]) -> None:
    result = ingest._fetch_odata_entities(URL, _paged_getter({URL: {"value": []}}))
    assert result == []
    assert events == []


def test_skip_paging_with_count_keeps_first_page_total(events: list[tuple[Any, ...]]) -> None:
    seen: list[str] = []
    second = ingest._with_skip(URL, 2)
    pages = {
        URL: {"@odata.count": 3, "value": _entities(1, 2)},
        second: {"@odata.count": 3, "value": _entities(3)},
    }
    result = ingest._fetch_odata_entities(URL, _paged_getter(pages, seen))
    assert result == _entities(1, 2, 3)
    assert seen == [URL, second]
    assert events == [
        ("init", 3, ingest.INGEST_LABEL, 0),
        ("start",),
        ("update", 2),
        ("update", 3),
        ("complete",),
    ]


def test_reporter_is_completed_when_getter_fails_mid_pagination(events: list[tuple[Any, ...]]) -> None:
    next_url = "http://example/odata/Locations?$skip=2"

    def getter(url: str) -> dict[str, Any]:
        if url == URL:
            return {"@odata.count": 5, "@odata.nextLink": next_url, "value": _entities(1, 2)}
        raise RuntimeError("server stalled")

    with pytest.raises(RuntimeError, match="server stalled"):
        ingest._fetch_odata_entities(URL, getter)
    assert events == [
        ("init", 5, ingest.INGEST_LABEL, 0),
        ("start",),
        ("update", 2),
        ("complete",),
    ]


def test_failure_before_any_reporter_leaves_no_reporter(events: list[tuple[Any, ...]]) -> None:
    def getter(url: str) -> dict[str, Any]:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        ingest._fetch_odata_entities(URL, getter)
    assert events == []
