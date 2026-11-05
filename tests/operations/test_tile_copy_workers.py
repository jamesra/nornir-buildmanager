"""Copy-stage worker count for the two-stage tile save, and backpressure when the destination is slow."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest
from nornir_buildmanager.operations import tile as tile_ops


@pytest.fixture(autouse=True)
def _clear_copy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tile_ops._TILE_COPY_WORKERS_ENV, raising=False)


def test_local_destination_keeps_small_default(tmp_path) -> None:
    assert not tile_ops._is_network_path(str(tmp_path))
    assert tile_ops._tile_copy_worker_count(str(tmp_path)) == tile_ops._TILE_COPY_WORKERS_DEFAULT


def test_unknown_destination_keeps_small_default() -> None:
    assert tile_ops._tile_copy_worker_count() == tile_ops._TILE_COPY_WORKERS_DEFAULT


def test_unc_path_is_network() -> None:
    assert tile_ops._is_network_path(r"\\storage\Data\Volumes\RC2")
    assert tile_ops._is_network_path("//storage/Data/Volumes/RC2")


def test_network_destination_uses_network_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tile_ops, "_is_network_path", lambda path: True)
    assert tile_ops._tile_copy_worker_count("Y:/Volumes/RC2") == tile_ops._TILE_COPY_WORKERS_NETWORK_DEFAULT


def test_env_overrides_network_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tile_ops, "_is_network_path", lambda path: True)
    monkeypatch.setenv(tile_ops._TILE_COPY_WORKERS_ENV, "5")
    assert tile_ops._tile_copy_worker_count("Y:/Volumes/RC2") == 5


def test_slow_destination_blocks_the_producer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With copies stalled, the producer stops after a bounded number of tiles; all tiles land once copies resume."""
    release = threading.Event()
    real_copy = tile_ops.nornir_shared.files.copy_file

    def stalled_copy(src: str, dst: str) -> None:
        release.wait()
        real_copy(src, dst)

    monkeypatch.setattr(tile_ops.nornir_shared.files, "copy_file", stalled_copy)
    encode_workers, copy_workers, total = 2, 1, 60
    pipeline = tile_ops._TwoStageTileSavePipeline(encode_workers=encode_workers, copy_workers=copy_workers, bpp=8,
                                                  optimize=False)
    dest = tmp_path / "dest"
    temp = tmp_path / "temp"
    dest.mkdir()
    temp.mkdir()
    tile = np.full((16, 16), 0.5, dtype=np.float32)
    submitted = 0

    def produce() -> None:
        nonlocal submitted
        for i in range(total):
            pipeline.submit(str(dest / f"{i}.png"), str(temp / f"{i}.png"), tile)
            submitted += 1
            while pipeline.active_encode_count >= pipeline.max_active_tasks:
                pipeline.wait_one_encode()

    producer = threading.Thread(target=produce)
    producer.start()
    time.sleep(1.0)
    # in flight: active encodes + the copy queue + the copy worker's current job
    bound = pipeline.max_active_tasks + 2 * copy_workers + copy_workers
    assert submitted <= bound
    assert producer.is_alive()
    release.set()
    producer.join(timeout=30)
    pipeline.finish()
    assert len(list(dest.iterdir())) == total
