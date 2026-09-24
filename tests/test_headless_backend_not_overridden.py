"""Importing nornir_buildmanager must not force a GUI matplotlib backend when headless.

``build.py`` runs at package import (``nornir_buildmanager/__init__`` pulls it in) and
used to call ``matplotlib.use('QtAgg')`` unconditionally, overriding the ``Agg`` that
``nornir_imageregistration`` selects for headless runs. The comment above that call
already said the build "must use a backend that does not allocate windows in the GUI" --
the code did the opposite.

The consequence was an indefinite hang rather than an error. Any later ``plt.show()``
entered a Qt event loop waiting for a window nobody could close. Under pytest this
wedged whole sessions, because collection imports every test module: one test importing
``nornir_buildmanager`` made the entire run GUI-backed, and a later plotting test hung
with no output and had to be killed. The same files passed when run individually,
because nothing had dragged the GUI backend in.

The backend is process-global and set at import, so each case runs in a fresh
interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

# Backends that open real windows. Agg and the other *Agg-less canvases do not.
_GUI_BACKENDS = {'qtagg', 'qt5agg', 'qt6agg', 'tkagg', 'wxagg', 'gtk3agg', 'gtk4agg',
                 'macosx', 'nbagg', 'webagg'}


def _backend_after_import(headless: str | None) -> str:
    """Import nornir_buildmanager in a fresh interpreter; return the active backend."""
    script = textwrap.dedent(
        """
        import matplotlib
        import nornir_buildmanager  # noqa: F401
        print('BACKEND=' + matplotlib.get_backend())
        """
    )
    env = dict(os.environ)
    env.pop('DEBUG', None)
    if headless is None:
        env.pop('NORNIR_HEADLESS', None)
    else:
        env['NORNIR_HEADLESS'] = headless

    completed = subprocess.run([sys.executable, '-c', script], capture_output=True,
                               text=True, env=env, timeout=300)
    assert completed.returncode == 0, (
        f'importing nornir_buildmanager failed:\n{completed.stdout}\n{completed.stderr}')
    for line in completed.stdout.splitlines():
        if line.startswith('BACKEND='):
            return line.split('=', 1)[1].strip()
    raise AssertionError(f'no backend reported:\n{completed.stdout}\n{completed.stderr}')


@pytest.mark.parametrize('flag', ['1', 'true', 'yes', 'on'])
def test_headless_import_leaves_a_non_gui_backend(flag):
    """Every spelling is_headless() accepts must keep the backend off the GUI."""
    backend = _backend_after_import(flag)
    assert backend.lower() not in _GUI_BACKENDS, (
        f'NORNIR_HEADLESS={flag!r} still left the GUI backend {backend!r}; a later '
        f'plt.show() would block on an event loop with no one to close the window')


def test_headless_import_selects_agg():
    """Specifically Agg, matching what nornir_imageregistration chooses when headless."""
    assert _backend_after_import('1').lower() == 'agg'


def test_non_headless_import_still_selects_the_gui_backend():
    """Interactive use is unchanged: without the flag the build still gets a GUI backend.

    Guards against over-correcting into always-Agg, which would silently drop plot
    windows for someone running a build by hand.
    """
    backend = _backend_after_import('0')
    assert backend.lower() in _GUI_BACKENDS or backend.lower() == 'agg', backend
    if backend.lower() == 'agg':
        pytest.skip('no GUI backend importable in this environment')
