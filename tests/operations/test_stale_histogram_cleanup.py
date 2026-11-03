"""Stale ``Histogram`` elements (data and image files both gone) are dropped; others stay.

RC3's Raw8 filters kept a ``Histogram Type="_Thr10.0"`` from a 2022-05 prune after its
files were deleted, and MosaicReport logged ~810 "Missing image file" errors. Covers the
one-time cleanup script and the Histogram stage, which now drops such elements for other
thresholds when it writes the current one. Histograms whose files exist are kept, so a
filter may hold several thresholds.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from xml.etree import ElementTree

import nornir_buildmanager.volumemanager
from nornir_buildmanager.operations import histogram_cleanup, tile

_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'prune_stale_histogram_entries.py'
_CHECKSUM = 'fd85444b9650c45580426a7e17f9d4fa'


def _histogram(type_: str, *, transform_derived: bool = True) -> str:
    stem = 'RawDataHistogram' if not transform_derived else f'Histogram{type_}'
    transform = (f'InputTransform="Prune" InputTransformChecksum="{_CHECKSUM if type_ == "_Thr2500.0" else "old"}" '
                 f'InputTransformType="{type_}" ') if transform_derived else ''
    return (f'<Histogram {transform}Type="{type_}" Version="1.0">'
            f'<Image Path="{stem}.png" Version="1.0" MinIntensityCutoff="0" MaxIntensityCutoff="255" Gamma="1.0" />'
            f'<Data Path="{stem}.xml" Version="1.0" />'
            f'<AutoLevelHint UserRequestedGamma="" UserRequestedMaxIntensityCutoff="" '
            f'UserRequestedMinIntensityCutoff="" Version="1.0" /></Histogram>')


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _volume(root: Path) -> Path:
    """One section whose Raw8 filter holds the RC3 mix of histograms.

    - ``_Thr2500.0``: current prune threshold, files present.
    - ``_Thr500.0``: another threshold, files present (kept: switchable).
    - ``_Thr10.0``: old threshold, both files gone (stale).
    - ``_Thr5.0``: data present, image gone (kept; a stage regenerates the image).
    - ``RawDataHistogram``: import histogram, both files gone.
    """
    vol = root / 'vol'
    _write(vol / 'VolumeData.xml', f'<Volume Name="vol" Path="{vol}"><Block_Link Name="TEM" Path="TEM" /></Volume>')
    _write(vol / 'TEM' / 'VolumeData.xml',
           '<Block Name="TEM" Path="TEM"><Section_Link Name="0001" Number="1" Path="0001" /></Block>')
    _write(vol / 'TEM' / '0001' / 'VolumeData.xml',
           '<Section Name="0001" Number="1" Path="0001"><Channel_Link Name="TEM" Path="TEM" /></Section>')
    channel = vol / 'TEM' / '0001' / 'TEM'
    _write(channel / 'VolumeData.xml',
           f'<Channel Name="TEM" Path="TEM"><Transform Checksum="{_CHECKSUM}" Name="Prune" '
           f'Path="Prune_Thr2500.0.mosaic" Threshold="2500" Type="_Thr2500.0" Version="1.0" />'
           f'<Filter_Link Name="Raw8" Path="Raw8" /></Channel>')
    _write(channel / 'Prune_Thr2500.0.mosaic', '')
    histograms = ''.join(_histogram(t) for t in ('_Thr2500.0', '_Thr500.0', '_Thr10.0', '_Thr5.0'))
    histograms += _histogram('RawDataHistogram', transform_derived=False)
    raw8 = channel / 'Raw8'
    _write(raw8 / 'VolumeData.xml', f'<Filter BitsPerPixel="8" Name="Raw8" Path="Raw8" Version="1.0">{histograms}'
                                    f'<TilePyramid_Link Path="TilePyramid" ImageFormatExt=".png" LevelFormat="%03d" '
                                    f'NumberOfTiles="0" Type="stage" Version="1.0" /></Filter>')
    _write(raw8 / 'TilePyramid' / 'VolumeData.xml',
           '<TilePyramid Path="TilePyramid" ImageFormatExt=".png" LevelFormat="%03d" NumberOfTiles="0" Type="stage" '
           'Version="1.0"><Level Downsample="1" Path="001" Version="1.0" /></TilePyramid>')
    (raw8 / 'TilePyramid' / '001').mkdir()
    for name in ('Histogram_Thr2500.0.xml', 'Histogram_Thr2500.0.png', 'Histogram_Thr500.0.xml',
                 'Histogram_Thr500.0.png', 'Histogram_Thr5.0.xml'):
        _write(raw8 / name, 'x')
    return vol


def _raw8_types(vol: Path) -> set[str]:
    root = ElementTree.parse(vol / 'TEM' / '0001' / 'TEM' / 'Raw8' / 'VolumeData.xml').getroot()
    return {h.get('Type') for h in root.findall('Histogram')}


def _load_filter(vol: Path):
    volume = nornir_buildmanager.volumemanager.VolumeManager.Load(str(vol))
    return next(volume.findall('Block/Section/Channel/Filter')), next(volume.findall('Block/Section/Channel'))


def _load_script():
    spec = importlib.util.spec_from_file_location('prune_stale_histogram_entries', _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_histograms_missing_both_files_are_stale(tmp_path: Path) -> None:
    filter_node, _channel = _load_filter(_volume(tmp_path))

    every = {h.get('Type') for h in histogram_cleanup.stale_histograms(filter_node)}
    derived = {h.get('Type') for h in histogram_cleanup.stale_histograms(filter_node, transform_derived_only=True)}
    kept = {h.get('Type') for h in histogram_cleanup.stale_histograms(filter_node, keep_type='_Thr10.0')}

    assert every == {'_Thr10.0', 'RawDataHistogram'}
    assert derived == {'_Thr10.0'}
    assert kept == {'RawDataHistogram'}


def test_cleanup_script_dry_run_changes_nothing_then_apply_removes_and_backs_up(tmp_path: Path) -> None:
    vol = _volume(tmp_path)
    raw8_xml = vol / 'TEM' / '0001' / 'TEM' / 'Raw8' / 'VolumeData.xml'
    original = raw8_xml.read_bytes()
    script = _load_script()

    dry = script.run(str(vol), apply=False, backup_dir=None)
    assert raw8_xml.read_bytes() == original
    assert (dry['stale'], dry['filters_changed'], dry['image_only_missing']) == (2, 1, 1)

    backup = tmp_path / 'backup'
    applied = script.run(str(vol), apply=True, backup_dir=str(backup))
    assert applied['stale'] == 2
    assert _raw8_types(vol) == {'_Thr2500.0', '_Thr500.0', '_Thr5.0'}
    assert (backup / 'TEM' / '0001' / 'TEM' / 'Raw8' / 'VolumeData.xml').read_bytes() == original
    assert sorted(os.listdir(raw8_xml.parent)) == sorted(
        ['TilePyramid', 'VolumeData.xml', 'VolumeData.xml.backup.xml', 'Histogram_Thr2500.0.xml', 'Histogram_Thr2500.0.png',
         'Histogram_Thr500.0.xml', 'Histogram_Thr500.0.png', 'Histogram_Thr5.0.xml']), 'no file deleted'

    assert script.run(str(vol), apply=False, backup_dir=None)['stale'] == 0


def test_histogram_stage_drops_other_thresholds_with_no_files(tmp_path: Path) -> None:
    vol = _volume(tmp_path)
    filter_node, channel = _load_filter(vol)
    transform = channel.GetChildByAttrib('Transform', 'Name', 'Prune')

    saved = list(tile.HistogramFilter({}, filter_node, 1, transform))
    assert filter_node in saved
    filter_node.Save()

    # The import-time RawDataHistogram is not a threshold, so the stage leaves it to the script.
    assert _raw8_types(vol) == {'_Thr2500.0', '_Thr500.0', '_Thr5.0', 'RawDataHistogram'}
