"""Micro-benchmark for the new-member scan in update._rebuild_watermarks (category 19).

For each planned crop with a prior watermark, the old form tested every planned location id
against the prior ``member_ids`` list (linear scan); the new form tests against a set built
once per plan. Setup is outside the timed function. Sizes: prior members and planned ids of
300, 2000 and 5000 (planned ids overlap the prior ids except for 10% new ones).
"""

from __future__ import annotations

import pyperf


def old_extra(location_ids: list[int], member_ids: list[int]) -> list[int]:
    return [i for i in location_ids if i not in member_ids]


def new_extra(location_ids: list[int], member_ids: list[int]) -> list[int]:
    prior_ids = set(member_ids)
    return [i for i in location_ids if i not in prior_ids]


def main() -> None:
    runner = pyperf.Runner()
    for n in (300, 2000, 5000):
        members = list(range(n))
        planned = list(range(n // 10, n + n // 10))
        runner.bench_func(f"rebuild_extra_old_n{n}", old_extra, planned, members)
        runner.bench_func(f"rebuild_extra_new_n{n}", new_extra, planned, members)


if __name__ == "__main__":
    main()
