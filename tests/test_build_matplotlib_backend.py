"""nornir-build always plots with Agg (2026-11-05).

On a desktop (no NORNIR_HEADLESS) build.py used to switch to QtAgg, overriding even MPLBACKEND=Agg, and stages
that plot from worker threads crashed the process (ImportIDoc's histogram; RC2 1226 Mosaic after the layout SVG).
Checked in a subprocess because the backend is chosen once, at import.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _backend(extra_env: dict[str, str]) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ('NORNIR_HEADLESS', 'MPLBACKEND', 'NORNIR_GUI_PLOTS')}
    env.update(extra_env)
    env.setdefault('DISPLAY', ':0')  # look like a desktop on Linux too
    result = subprocess.run([sys.executable, '-c',
                             'import nornir_buildmanager.build, matplotlib; print(matplotlib.get_backend())'],
                            env=env, capture_output=True, text=True, timeout=300, check=True)
    return result.stdout.strip().splitlines()[-1].lower()


def test_desktop_build_uses_agg():
    assert _backend({}) == 'agg'


def test_mplbackend_agg_is_honoured():
    assert _backend({'MPLBACKEND': 'Agg'}) == 'agg'
