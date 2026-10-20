"""Micro-benchmark for the ``-Sections`` filter in pipeline.ExportRemoteSections (category 18).

The filter keeps the candidate sections named by ``-Sections``. The old form rebuilt
``set(wanted)`` for every candidate; the new form builds it once. Setup is outside the timed
function. Sizes: a 3000-section volume with ``-Sections 1-3000`` (every section named) and a
1000-section selection from it, as a full-volume export would pass.
"""

from __future__ import annotations

import pyperf


def old_filter(candidates: list[int], wanted: list[int]) -> list[int]:
    return [z for z in candidates if z in set(wanted)]


def new_filter(candidates: list[int], wanted: list[int]) -> list[int]:
    wanted_set = set(wanted)
    return [z for z in candidates if z in wanted_set]


def main() -> None:
    runner = pyperf.Runner()
    for total, picked in ((3000, 3000), (3000, 1000), (300, 50)):
        candidates = list(range(1, total + 1))
        wanted = list(range(1, picked + 1))
        runner.bench_func(f"section_filter_old_n{total}_w{picked}", old_filter, candidates, wanted)
        runner.bench_func(f"section_filter_new_n{total}_w{picked}", new_filter, candidates, wanted)


if __name__ == "__main__":
    main()
