"""Micro-benchmark for StosMapNode.IsValid (category 19).

IsValid walks every mapped section and tests it against the sections already seen, then
tests each control against the NonStosSectionNumbers text. Times a valid map (no duplicate
controls, nothing banned, duplicates allowed), which IsValid leaves unchanged between calls.
Sizes: a mid-size volume (500 sections) and a large one (2000 sections), one Mapping per
control section mapping its neighbour, as the adjacent-section pipeline builds them, under a
Block whose NonStosSectionNumbers lists a few sections outside the map.
"""

from __future__ import annotations

import pyperf
from nornir_buildmanager.volumemanager import MappingNode, XElementWrapper
from nornir_buildmanager.volumemanager.stosmapnode import StosMapNode


def _bench_case(runner: pyperf.Runner, num_sections: int) -> None:
    """Build a Block holding NonStosSectionNumbers and a StosMap of num_sections sections."""
    block = XElementWrapper(tag='Block')
    non_stos = XElementWrapper(tag='NonStosSectionNumbers')
    non_stos.text = ','.join(str(n) for n in range(num_sections + 10, num_sections + 20))
    block.append(non_stos)

    stos_map = StosMapNode.Create(Name='PotentialRegistrationChain')
    for control in range(1, num_sections):
        stos_map.append(MappingNode.Create(control, [control + 1]))
    block.append(stos_map)

    def worker() -> None:
        stos_map.IsValid()

    runner.bench_func(f"stosmap_isvalid_n{num_sections}", worker)


def main() -> None:
    """Run each size as its own pyperf benchmark."""
    runner = pyperf.Runner()
    for num_sections in (500, 2000):
        _bench_case(runner, num_sections)


if __name__ == "__main__":
    main()
