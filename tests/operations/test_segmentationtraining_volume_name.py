"""The volume name comes from -VolumeName, the VikingXML, then the annotation server, else it is an error."""

from __future__ import annotations

from pathlib import Path

import pytest

from nornir_buildmanager.exceptions import NornirUserException
from nornir_buildmanager.operations.segmentationtraining import pipeline
from nornir_buildmanager.operations.segmentationtraining.grouping import (
    volume_name_from_odata_url,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://websvc.codepharm.net/NeitzTemporalMonkeyTokenTest/OData", "TemporalMonkey"),
        ("https://websvc.codepharm.net/NeitzCPED/OData", "CPED"),
        ("https://websvc.codepharm.net/NeitzInferiorMonkey/OData/", "InferiorMonkey"),
        ("https://websvc.codepharm.net/Other/odata", "Other"),
        ("https://websvc.codepharm.net/Neitz/OData", "Neitz"),
        ("https://websvc.codepharm.net/OData", None),
        ("https://websvc.codepharm.net/NeitzCPED/Other", None),
        ("", None),
        (None, None),
    ],
)
def test_volume_name_from_the_annotation_server_name(url: str | None, expected: str | None) -> None:
    assert volume_name_from_odata_url(url) == expected


def _server(monkeypatch: pytest.MonkeyPatch, url: str | None) -> None:
    monkeypatch.setattr(pipeline, "odata_url_from_output", lambda output: url)


def test_explicit_name_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _server(monkeypatch, "https://h/NeitzCPED/OData")
    assert pipeline._resolve_export_volume_name("Mine", "FromXml", str(tmp_path)) == "Mine"


def test_volume_own_name_beats_the_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _server(monkeypatch, "https://h/NeitzCPED/OData")
    assert pipeline._resolve_export_volume_name(None, "FromXml", str(tmp_path)) == "FromXml"


def test_annotation_server_name_is_the_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _server(monkeypatch, "https://h/NeitzTemporalMonkeyTokenTest/OData")
    assert pipeline._resolve_export_volume_name(None, None, str(tmp_path)) == "TemporalMonkey"


@pytest.mark.parametrize("url", [None, "https://h/OData"])
def test_no_name_anywhere_asks_for_volume_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, url: str | None) -> None:
    _server(monkeypatch, url)
    with pytest.raises(NornirUserException, match="-VolumeName"):
        pipeline._resolve_export_volume_name(None, None, str(tmp_path))
