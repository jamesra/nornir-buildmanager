"""Micro-benchmark for StosMapNode.ClearMissingSections (category 19).

block.py passes ``existing_sections`` as a list of section numbers, so a membership test
per Mapping scaled with mappings x sections. Times the common re-run case (every control
section still exists, nothing removed), which leaves the node unchanged between calls.
Sizes: a mid-size volume (500 sections) and a large one (2000 sections), one Mapping per
control section mapping its neighbour, as the adjacent-section pipeline builds them.
"""

from __future__ import annotations

import pyperf
from nornir_buildmanager.volumemanager import MappingNode
from nornir_buildmanager.volumemanager.stosmapnode import StosMapNode


def _bench_case(runner: pyperf.Runner, num_sections: int) -> None:
    stos_map = StosMapNode.Create(Name='PotentialRegistrationChain')
    for control in range(1, num_sections):
        stos_map.append(MappingNode.Create(control, [control + 1]))

    existing_sections = list(range(1, num_sections + 1))

    def worker() -> None:
        stos_map.ClearMissingSections(existing_sections)

    runner.bench_func(f"clear_missing_sections_n{num_sections}", worker)


def main() -> None:
    runner = pyperf.Runner()
    for num_sections in (500, 2000):
        _bench_case(runner, num_sections)


if __name__ == "__main__":
    main()
