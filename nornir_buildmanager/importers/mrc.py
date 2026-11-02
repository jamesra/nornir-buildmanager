"""
Created on Apr 9, 2019

@author: u0490822
"""
from __future__ import annotations

import collections
import enum
import errno
import struct
import sys
import tempfile
from collections.abc import Callable, Sequence

from PIL import Image
import PIL.Image
from typing import Literal

import numpy
import numpy as np
from numpy.typing import DTypeLike, NDArray

import nornir_pools
import nornir_buildmanager.importers.serialem_utils as serialem_utils
import nornir_buildmanager.importers.shared as shared
from nornir_buildmanager.importers import source_staging
from nornir_buildmanager.volumemanager import *
import nornir_imageregistration
from nornir_imageregistration import noise_crush
from nornir_imageregistration.noise_crush import CrushParams
from nornir_shared.files import RemoveOutdatedFile
import nornir_shared.files as files
from nornir_shared.histogram import *
from nornir_shared.images import *
from . import GetFileNameForTileNumber
from nornir_buildmanager import NornirUserException


def Import(VolumeElement: VolumeNode, ImportPath: str, extension: str | None = None, *args, **kwargs):
    """Import the specified directory into the volume"""

    if extension is None:
        extension = 'mrc'

    MinCutoff = float(kwargs.get('Min'))  # type: ignore[arg-type]
    MaxCutoff = float(kwargs.get('Max'))  # type: ignore[arg-type]
    ContrastCutoffs = (MinCutoff, MaxCutoff)

    if not os.path.exists(ImportPath):
        raise ValueError(f"Import directory not found: {ImportPath}")

    CameraBpp = kwargs['CameraBpp'] if 'CameraBpp' in kwargs else None
    NoCrush = bool(kwargs.get('NoCrush', False))
    CrushK = kwargs.get('CrushK', None)

    FlipList = nornir_buildmanager.importers.GetFlipList(ImportPath)
    histogramFilename = os.path.join(ImportPath, nornir_buildmanager.importers.DefaultHistogramFilename)
    ContrastMap = nornir_buildmanager.importers.LoadOrCreateHistogramCutoffs(
        histogramFilename, tolerated_errnos=(errno.EROFS, errno.EACCES))

    matches = files.RecurseSubdirectoriesGenerator(ImportPath, RequiredFiles="*." + extension, ExcludeNames=[],
                                                   ExcludedDownsampleLevels=[])
    # Two MRCs that parse to one section number would both write into that section and leave it
    # mixing acquisitions (RC1: "0347.mrc" and "0347 2.mrc"), so keep one per section. A list of
    # paths, not of images.
    found_paths = [os.path.join(path, f) for (path, foundfiles) in matches for f in (foundfiles or [])]
    selected_paths = shared.SelectOneFilePerSection(found_paths)
    # Copy the next MRCs to local disk while the current one exports (source_staging); pixel reads use the copy.
    with source_staging.SourcePrefetcher(selected_paths, validate=MRCFile.HealthProblem,
                                         already_done=lambda p: _SectionTilesExported(VolumeElement.FullPath, p)) as staging:
        for file_fullpath in selected_paths:
            prettyoutput.CurseString("MRCImport", f"Importing {file_fullpath}")
            problem = MRCFile.HealthProblem(file_fullpath)
            if problem is not None:
                # Skip it rather than fail mid-export and leave a partial section.
                prettyoutput.LogErr(f"UNHEALTHY {file_fullpath}: {problem}; section skipped")
                staging.release(file_fullpath)
                continue
            try:
                yield from MRCImport.ToMosaic(VolumeElement,
                                              mrc_fullpath=file_fullpath,
                                              ContrastCutoffs=ContrastCutoffs,
                                              FlipList=FlipList,
                                              CameraBpp=CameraBpp,
                                              ContrastMap=ContrastMap,
                                              NoCrush=NoCrush,
                                              CrushK=CrushK,
                                              read_path=lambda p=file_fullpath: staging.acquire(p),
                                              )
            finally:
                staging.release(file_fullpath)

    nornir_pools.ReleaseStagePools()


def _SectionTilesExported(volume_path: str, mrc_fullpath: str) -> bool:
    """True when some filter of the MRC's section already holds a level-1 tile for every tile in the MRC.

    Reads only the MRC header and checks file names on disk (``<volume>/TEM/<section>/TEM/<filter>/TilePyramid/001``),
    so the import's staging can skip a finished section without copying it. Any doubt (unparsable name or header,
    no filter yet) answers False and the import proceeds as usual.
    """
    try:
        section_number = shared.GetSectionInfo(mrc_fullpath).number
        mrc_obj = MRCFile.Load(mrc_fullpath)
        num_tiles = int(mrc_obj.num_tiles)  # type: ignore[arg-type]
        mrc_obj.mrc.close()
    except Exception:  # noqa: BLE001 - not knowing means not done
        return False

    section_dir = ('%' + nornir_buildmanager.templates.Current.SectionFormat) % section_number
    channel_dir = os.path.join(volume_path, 'TEM', section_dir, 'TEM')
    if not os.path.isdir(channel_dir):
        return False
    tile_names = [GetFileNameForTileNumber(tile_number=i, ext='.png') for i in range(num_tiles)]
    for filter_name in os.listdir(channel_dir):
        level_dir = os.path.join(channel_dir, filter_name, TilePyramidNode.DefaultPath,
                                 nornir_buildmanager.templates.Current.LevelFormat % 1)
        if os.path.isdir(level_dir) and all(os.path.exists(os.path.join(level_dir, n)) for n in tile_names):
            return True
    return False


# SerialEM writes tiles with the image origin in the lower-left corner, so the
# raw rows arrive bottom-up relative to the orientation the rest of the pipeline
# expects. This is a fixed convention conversion applied to every section, and
# it was calibrated against the original RC1 dataset.
DEFAULT_FLIP_UD: bool = True


def flip_ud_for_section(section_number: int, flip_list: list[int] | None) -> bool:
    """Return the vertical pixel-flip flag for a section.

    FlipList.txt deliberately does not participate. The listed-section flip is a
    coordinate concern: pmg.py mirrors tile Y positions and leaves pixels alone,
    which is the established contract. Inverting this pixel flip per section
    would mirror tile contents without reordering them, tearing every horizontal
    seam.

    # TODO: wire FlipList.txt to the mosaic coordinates in CreateMosaic, matching
    # pmg.py. Needs a flipped dataset to verify against; no FlipList.txt exists
    # in nornir-testdata today.
    """
    del section_number, flip_list
    return DEFAULT_FLIP_UD


def _wait_for_tile_tasks(tasks: Sequence, operation: str) -> None:
    """Wait for the tile tasks this caller submitted, in submission order.

    Waiting on our own tasks rather than calling ``pool.shutdown()`` is the point. These
    callers use pools they did not create, and ``shutdown()`` both sets ``shutdown_event``
    and drops the pool from the registry, so it is not a private teardown. Measured against
    ``GetGlobalThreadPool()``, which has 26 submission sites across the three packages:

      * a holder that cached the reference gets ``AssertionError`` from ``add_task`` --
        bare, with no message, and compiled out entirely under ``-O``
      * a caller that re-fetches gets a *different*, working pool, so the breakage is
        invisible unless you happened to cache

    ``tile.py`` already commented out its own ``pool.shutdown()`` calls in favour of
    dropping the reference; this brings the MRC importer in line.

    A failing tile is logged with its number and re-raised, matching
    ``CalculateHistogram``. This is a deliberate behaviour change: ``shutdown()`` reached
    ``wait_completion()``, which is ``tasks.join()`` and does not re-raise, so a tile that
    failed to export was skipped silently and left a pyramid short of the tile count
    recorded against it.
    """
    for task in tasks:
        try:
            task.wait()
        except Exception as e:
            prettyoutput.LogErr(f"Failed to {operation} tile #{task.name}\n{e}")
            raise


class MRCImport:
    """
    Imports an .MRC file into a volume
    """

    _temp_dir: str  # Temporary directory to store numpy arrays of each tile image in the MRC file.  Used with a context manager to clean up the temporary files

    @classmethod
    def ToMosaic(cls, VolumeObj: VolumeNode, mrc_fullpath: str,
                 ContrastCutoffs: tuple[float, float],
                 Extension: str | None = None, OutputImageExt: str | None = None,
                 TileOverlap=None, TargetBpp: int | None = None, FlipList: list[int] | None = None,
                 ContrastMap: dict[int, nornir_buildmanager.importers.ContrastValue] | None = None,
                 CameraBpp: int | None = None,
                 debug: bool | None = None,
                 NoCrush: bool = False,
                 CrushK: float | None = None,
                 read_path: Callable[[], str] | None = None):
        """Import one .mrc file as a section.

        Tiles deeper than 8 bits are crushed by default (``nornir_imageregistration.noise_crush``)
        into a ``Raw8`` filter of section-wide noise-step codes. ``NoCrush`` keeps the legacy
        contrast-stretched ``Raw16`` filter, as does an existing uncrushed ``Raw16`` import.

        :param read_path: Returns the path to export tiles from, typically a staged local copy of
            ``mrc_fullpath`` (source_staging); called only when tiles are missing. Names, notes, logs,
            caches, freshness checks and the noise sample always use ``mrc_fullpath``.
        """

        if OutputImageExt is None:
            OutputImageExt = '.png'

        if TargetBpp is None:
            TargetBpp = 8

        if ContrastMap is None:
            ContrastMap = {}

        OutputPath = VolumeObj.FullPath

        os.makedirs(OutputPath, exist_ok=True)
        logger = logging.getLogger(__name__ + '.' + str(cls.__name__) + "ToMosaic")
        prettyoutput.CurseString('Stage', "SerialEM to Mosaic " + str(mrc_fullpath))

        mrc_fullpath = serialem_utils.GetPathWithoutSpaces(mrc_fullpath)

        source_dir = os.path.dirname(mrc_fullpath)

        BlockObj = BlockNode.Create('TEM')
        [saveBlock, BlockObj] = VolumeObj.UpdateOrAddChild(BlockObj)
        if saveBlock:
            (yield VolumeObj)

        mrcfile: MRCFile = MRCFile.Load(mrc_fullpath)
        if CameraBpp is None:
            CameraBpp = mrcfile.bytes_per_pixel * 8

        ExistingSectionInfo = shared.GetSectionInfo(mrc_fullpath)
        SectionNumber = ExistingSectionInfo.number
        SectionPath = ('%' + nornir_buildmanager.templates.Current.SectionFormat) % ExistingSectionInfo.number
        SectionName = ('%' + nornir_buildmanager.templates.Current.SectionFormat) % ExistingSectionInfo.number

        sectionObj = SectionNode.Create(SectionNumber,
                                        SectionName,
                                        SectionPath)
        [saveSection, sectionObj] = BlockObj.UpdateOrAddChildByAttrib(sectionObj, 'Number')
        sectionObj.Name = SectionName

        if saveSection:
            (yield BlockObj)

        [saveChannel, channelObj] = sectionObj.UpdateOrAddChildByAttrib(ChannelNode.Create('TEM'), 'Name')
        if saveChannel:
            (yield sectionObj)

        try:
            shared.TryAddNotes(channelObj, source_dir, logger)
        except NornirUserException:
            pass

        try:
            serialem_utils.TryAddLogs(channelObj, source_dir, logger)
        except NornirUserException:
            pass

        [added_scale, ScaleObj] = channelObj.SetScale(mrcfile.scale)
        if added_scale:
            (yield channelObj)

        bpp = CameraBpp
        FilterName = 'Raw' + str(mrcfile.bytes_per_pixel * 8)
        if TargetBpp is None:
            FilterName = 'Raw'

        # Cache files follow NORNIR_IMPORT_CACHE_PATH, as for IDoc imports, so a read-only source works.
        # A Histogram-N.xml already beside the MRC, from an import made before cache sidecars, is
        # reused when it is newer than the MRC: computing one reads the whole file (~32 GB on RC1).
        histogram_filename = f'Histogram-{SectionNumber}.xml'
        histogram_cache_path = os.path.join(serialem_utils.get_import_cache_path(source_dir), histogram_filename)

        contrast_settings = shared.GetSectionContrastSettings(
            section_number=SectionNumber,
            contrast_map=ContrastMap,
            contrast_cutoffs=ContrastCutoffs,
            calculate_histogram=lambda: cls.CalculateHistogram(  # type: ignore[arg-type]
                mrc_obj=mrcfile, bpp=bpp),
            histogram_cache_path=histogram_cache_path,
            cache_inputs=[mrc_fullpath],
            histogram_stride=1,
            legacy_histogram_cache_path=os.path.join(source_dir, histogram_filename))

        contrast_settings = shared.MinMaxGamma(min=np.around(contrast_settings.min),
                                               max=np.around(contrast_settings.max),
                                               gamma=contrast_settings.gamma)

        requested_crush = None
        if not NoCrush and mrcfile.bytes_per_pixel > 1:
            requested_crush = shared.SectionCrushParams(
                shared.GetSectionNoise(histogram_cache_path, [mrc_fullpath], mrcfile.num_tiles,  # type: ignore[arg-type]
                                       mrcfile.get_tile_as_numpy_memmap),
                Histogram.Load(histogram_cache_path) if os.path.exists(histogram_cache_path) else None,
                k=nornir_imageregistration.noise_crush.DEFAULT_CRUSH_K if CrushK is None else float(CrushK))
            crushed_filter_name = shared.CrushedFilterName(requested_crush)
            legacy_filter = channelObj.GetFilter(FilterName)
            if crushed_filter_name != FilterName and legacy_filter is not None and \
                    CrushParams.from_attributes(legacy_filter.attrib) is None and \
                    not channelObj.HasFilter(crushed_filter_name):
                # Same rule as ResolveCrushMode: an existing uncrushed import is not replaced.
                logger.info("Keeping existing uncrushed filter %s; remove it to crush this section",
                            legacy_filter.FullPath)
                requested_crush = None
            else:
                FilterName = crushed_filter_name

        (crush_params, crush_removed) = shared.ResolveCrushMode(channelObj, FilterName, requested_crush, logger)

        if crush_params is not None:
            # Crushed tiles do not depend on the contrast settings; they become hints in code units.
            filter_contrast = shared.MinMaxGamma(min=shared.CountsToCrushCodes(contrast_settings.min, crush_params),
                                                 max=shared.CountsToCrushCodes(contrast_settings.max, crush_params),
                                                 gamma=contrast_settings.gamma)
            contrast_mismatch = crush_removed
            prettyoutput.Log(f"Crushing section {SectionNumber}: sigma {crush_params.sigma:.1f} counts, "
                             f"step {crush_params.step}, offset {crush_params.offset}, {crush_params.bits}-bit codes")
        else:
            filter_contrast = contrast_settings
            contrast_mismatch = crush_removed or channelObj.RemoveFilterOnContrastMismatch(
                FilterName, contrast_settings.min, contrast_settings.max, contrast_settings.gamma)
        image_conversion_required = contrast_mismatch

        pool = nornir_pools.GetGlobalThreadPool()
        # _PlotHistogram(histogramFullPath, SectionNumber, ActualMosaicMin, ActualMosaicMax)
        histogram_creation_task = pool.add_task(histogram_cache_path,
                                                shared.PlotHistogram,
                                                histogram_cache_path,
                                                SectionNumber,
                                                contrast_settings.min,
                                                contrast_settings.max, force_recreate=contrast_mismatch)

        [added_filter, filterObj] = channelObj.UpdateOrAddChildByAttrib(FilterNode.Create(Name=FilterName), 'Name')
        filterObj.SetContrastValues(filter_contrast.min, filter_contrast.max, filter_contrast.gamma)
        if crush_params is not None:
            filterObj.BitsPerPixel = crush_params.bits
            shared.SetFilterCrushAttributes(filterObj, crush_params)
        else:
            filterObj.BitsPerPixel = bpp
        if added_filter:
            image_conversion_required = True

        StageTransformName = 'Stage'
        StageTransformFilename = StageTransformName + '.mosaic'
        StageTransformFullPath = os.path.join(channelObj.FullPath, StageTransformFilename)

        # Check to make sure our stage mosaic file is valid
        RemoveOutdatedFile(mrc_fullpath, StageTransformFullPath)

        (added_transform, transformObj) = channelObj.UpdateOrAddChildByAttrib(
            TransformNode.Create(Name=StageTransformName,
                                 Path=StageTransformFilename,
                                 Type='Stage'),
            'Path')
        if added_transform:
            (yield channelObj)

        (added_tilepyramid, PyramidNodeObj) = filterObj.UpdateOrAddChildByAttrib(TilePyramidNode.Create(Type='stage',
                                                                                                        NumberOfTiles=mrcfile.num_tiles),  # type: ignore[arg-type]
                                                                                 'Path')
        if added_tilepyramid:
            (yield filterObj)

        (added_level, LevelObj) = PyramidNodeObj.GetOrCreateLevel(1, GenerateData=False)
        if added_level:
            (yield PyramidNodeObj)

        assert LevelObj is not None
        os.makedirs(LevelObj.FullPath, exist_ok=True)

        if not os.path.exists(transformObj.FullPath):
            mosaicObj = cls.CreateMosaic(mrcfile, img_ext=OutputImageExt)
            mosaicObj.SaveToMosaicFile(transformObj.FullPath)
            (yield channelObj)

        # Wait for the staged local copy only when tiles are missing: a restarted import walks
        # finished sections too, and must not copy a 33 GB MRC to skip one.
        pixel_path = mrc_fullpath
        if read_path is not None and any(
                not os.path.exists(os.path.join(LevelObj.FullPath, GetFileNameForTileNumber(tile_number=i, ext=OutputImageExt)))
                for i in range(mrcfile.num_tiles)):  # type: ignore[arg-type]
            pixel_path = read_path()

        cls.ExportImages(pixel_path, LevelObj.FullPath, img_ext=OutputImageExt,
                         min_max_gamma=contrast_settings,
                         flip_ud=flip_ud_for_section(SectionNumber, FlipList),
                         crush_params=crush_params)

    def __init__(self):
        """
        Constructor
        """
        pass

    def __enter__(self):
        self._temp_dir = tempfile.mkdtemp()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        nornir_shared.files.rmtree(self._temp_dir)

    @staticmethod
    def cache_tiles(mrcfile: MRCFile, output_dir: str, img_ext: str, min_max_gamma: shared.MinMaxGamma | None):
        pool = nornir_pools.GetThreadPool("Import", num_threads=os.cpu_count() * 2)  # type: ignore[operator]
        tasks = [pool.add_task(str(iTile),
                               MRCImport.ExportImage,
                               mrcfile,
                               output_dir,
                               img_ext,
                               iTile,
                               min_max_gamma)
                 for iTile in range(0, mrcfile.num_tiles)]  # type: ignore[arg-type]

        _wait_for_tile_tasks(tasks, 'cache')

    @staticmethod
    def CalculateHistogram(mrc_obj: str | MRCFile, bpp: float):
        """Construct a composite histogram from all images in the MRC file"""
        if isinstance(mrc_obj, str):
            mrc_obj = MRCFile.Load(mrc_obj)

        composite_histogram = None

        pool = nornir_pools.GetGlobalThreadPool()
        # pool = nornir_pools.GetGlobalSerialPool()

        num_bins = 256
        if bpp >= 11:
            num_bins = 2048

        def consume(task):
            """Fold one finished tile into the composite and release its buffer."""
            nonlocal composite_histogram
            try:
                image_histogram = task.wait_return()
            except Exception as e:
                prettyoutput.LogErr(f"Failed to build histogram for tile #{task.iTile}\n{e}")
                raise

            if composite_histogram is None:
                composite_histogram = image_histogram
            else:
                composite_histogram.AddHistogram(image_histogram)

        def histogram_of_tile(iTile: int):
            """Open the tile inside the worker so no queued task holds a buffer.

            Passing the memmap as a task argument keeps it reachable for as long as
            the pool keeps the task, which outlives the call. Opening it here ties
            the buffer's lifetime to the worker, exactly as ExportImage does.
            """
            img = mrc_obj.get_tile_as_numpy_memmap(iTile)  # type: ignore[union-attr]
            return nornir_imageregistration.image_stats.HistogramOfArray(
                img, bpp=bpp, num_bins=num_bins)

        # Bounded sliding window, drained in submission order so the composite is
        # deterministic. Queueing every tile first meant one live tile buffer per
        # tile in the file, so peak memory scaled with tile count rather than cores.
        # Sized to the pool rather than the machine: queueing deeper than the pool
        # can run buys nothing.
        max_in_flight = max(1, getattr(pool, 'max_workers', None) or (os.cpu_count() or 1))
        pending = collections.deque()

        for iTile, tile_header in enumerate(mrc_obj.tile_meta):
            t = pool.add_task(str(iTile), histogram_of_tile, iTile)
            t.iTile = iTile  # type: ignore[attr-defined]
            pending.append(t)

            if len(pending) >= max_in_flight:
                consume(pending.popleft())

        while pending:
            consume(pending.popleft())

        return composite_histogram

    @classmethod
    def ExportImages(cls, mrc_obj: str | MRCFile, output_dir: str, img_ext: str,
                     min_max_gamma: shared.MinMaxGamma | None,
                     flip_ud: bool = DEFAULT_FLIP_UD,
                     crush_params: CrushParams | None = None):
        if isinstance(mrc_obj, str):
            mrc_obj = MRCFile.Load(mrc_obj)

        pool = nornir_pools.GetGlobalThreadPool()
        #pool = nornir_pools.GetThreadPool("Import", num_threads=os.cpu_count() * 2)
        #pool = nornir_pools.GetGlobalSerialPool()

        tasks = [pool.add_task(str(iTile),
                               cls.ExportImage,
                               mrc_obj,
                               output_dir,
                               img_ext,
                               iTile,
                               min_max_gamma,
                               flip_ud,
                               crush_params)
                 for iTile in range(0, mrc_obj.num_tiles)]  # type: ignore[arg-type]

        _wait_for_tile_tasks(tasks, 'export')

    @staticmethod
    def ExportImage(mrc_obj: str | MRCFile, output_dir: str, img_ext: str, iTile: int,
                    min_max_gamma: shared.MinMaxGamma | None = None,
                    flip_ud: bool = DEFAULT_FLIP_UD,
                    crush_params: CrushParams | None = None) -> bool:
        """Write one tile; crushed codes when *crush_params* is given, else the legacy contrast stretch."""
        if isinstance(mrc_obj, str):
            mrc_obj = MRCFile.Load(mrc_obj)

        filename = GetFileNameForTileNumber(tile_number=iTile, ext=img_ext)  # Pillow does not support 16-bit PNG
        output_fullpath = os.path.join(output_dir, filename)
        if nornir_shared.images.IsValidImage(output_fullpath):
            return False

        if crush_params is not None:
            img = mrc_obj.get_tile_as_numpy_memmap(iTile)
            if flip_ud:
                img = np.flipud(img)

            noise_crush.save_crushed_png(output_fullpath, noise_crush.crush(img, crush_params), crush_params)
            del img
        elif min_max_gamma is None:
            im = mrc_obj.get_tile_as_image(iTile)
            if flip_ud:
                # Orientation must not depend on whether contrast values were
                # supplied; the contrast branch below flips the same way.
                im = im.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            im.save(output_fullpath, compress_level=1)
        else:
            # This mess is here because we can't really trust the min/max pixel values reported in the MRC file for a lot of our old data
            # t = mrcfile._repair_out_of_bounds_pixels(iTile, 14)
            img = mrc_obj.get_tile_as_numpy_memmap(iTile)
            dt = img.dtype
            if flip_ud:
                # This was required to make the images match the orientation of the RC1 original dataset.
                img = np.flipud(img)

            # Quick correct out-of-bounds pixels
            outliers = img > min_max_gamma.max
            img_clipped = np.clip(img, min_max_gamma.min, min_max_gamma.max)
            img = img.astype(np.float32)
            # img[outliers] /= 2.0
            scale = np.iinfo(dt).max / min_max_gamma.max
            if min_max_gamma.min > 0:
                img = (img - min_max_gamma.min) * scale
            else:
                img *= scale

            img = img.round().astype(dt, copy=False)

            with Image.fromarray(img).convert(mode='I') as im:
                im.save(output_fullpath, compress_level=1)

            del img

        return True

    @classmethod
    def GetSectionContrastSettings(cls, mrcfile: MRCFile, SectionNumber: int,
                                   ContrastMap: dict[int, nornir_buildmanager.importers.ContrastValue],
                                   CameraBpp: int) -> shared.MinMaxGamma:
        """Clear and recreate the filters tile pyramid node if the filters contrast node does not match"""
        Gamma = 1.0

        minval = mrcfile.min_pixel_value
        maxval = mrcfile.max_pixel_value

        if CameraBpp is not None:
            camera_max_val = (1 << CameraBpp) - 1
            maxval = min(maxval, camera_max_val)  # type: ignore[arg-type]

        if SectionNumber in ContrastMap:
            minval = ContrastMap[SectionNumber].Min
            maxval = ContrastMap[SectionNumber].Max
            Gamma = ContrastMap[SectionNumber].Gamma

        return shared.MinMaxGamma(minval, maxval, Gamma)  # type: ignore[arg-type]

    @staticmethod
    def CreateMosaic(mrcfile, img_ext: str) -> nornir_imageregistration.Mosaic:
        mosaic = nornir_imageregistration.Mosaic()

        for (i, t) in enumerate(mrcfile.tile_meta):
            image_shape = mrcfile.img_shape
            pixel_position = t.pixel_coords
            tile_transform = nornir_imageregistration.transforms.factory.CreateRigidTransform(
                target_image_shape=image_shape,
                source_image_shape=image_shape,
                warped_offset=pixel_position,
                rangle=0)
            # tile_transform = nornir_imageregistration.transforms.factory.CreateRigidMeshTransform(
            #     target_image_shape=image_shape,
            #     source_image_shape=image_shape,
            #     rangle=0,
            #     warped_offset=pixel_position)
            tile_filename = GetFileNameForTileNumber(i, img_ext)
            mosaic.ImageToTransform[tile_filename] = tile_transform

        mosaic.TranslateToZeroOrigin()
        return mosaic


class MRCFile:
    """
    Reads a SerialEM mrc file
    http://bio3d.colorado.edu/imod/doc/mrc_format.txt
    """
    HeaderLength = 1024

    grid_dim: NDArray[np.integer] | None
    grid_cell_dim: NDArray[np.floating] | None
    pixel_spacing: NDArray[np.floating] | None
    min_pixel_value: float | None
    max_pixel_value: float | None
    mean_pixel_value: float | None
    extended_header_size: int | None
    tile_header_size: int | None
    tile_header_flags: int | None
    img_origin: NDArray[np.floating] | None
    imod_flags: int | None
    mapx: int | None
    mapy: int | None
    mapz: int | None
    tile_meta: list[MRCTileHeader]

    _temp_dir: str  # Temporary directory to store numpy arrays of each tile image in the MRC file.  Used with a context manager to clean up the temporary files

    @property
    def scale(self) -> Scale:
        return Scale(X=self.pixel_spacing[0],  # type: ignore[index]
                     Y=self.pixel_spacing[1],  # type: ignore[index]
                     Z=self.pixel_spacing[2])  # type: ignore[index]

    @staticmethod
    def IsBigEndian(Header: bytes) -> bool:
        """Returns true if the mrc header indicates the file is big endian"""
        (EndianStamp,) = struct.unpack('I', Header[0xD4:0xD8])
        if EndianStamp == 17:
            return True
        elif EndianStamp == 68:
            return False
        else:
            Warning('ENDIAN Value in MRC header incorrect, defaulting to Little Endian')
            return False

    @staticmethod
    def HealthProblem(filename: str) -> str | None:
        """Why ``filename`` is not a complete MRC, or None when its header parses and its size covers every tile.

        Cheap (one 1 KB header read): checked before staging and exporting a section, so a truncated file is
        reported up front instead of after a full copy and a partial export.
        """
        try:
            mrc_obj = MRCFile.Load(filename)
        except Exception as e:  # noqa: BLE001 - any header failure is the reason reported
            return f"header does not parse ({e})"
        try:
            expected = (MRCFile.HeaderLength + int(mrc_obj.extended_header_size or 0) +
                        int(mrc_obj.num_tiles) * int(mrc_obj.img_XDim) * int(mrc_obj.img_YDim) *  # type: ignore[arg-type]
                        mrc_obj.bytes_per_pixel)
            actual = os.path.getsize(filename)
        finally:
            mrc_obj.mrc.close()
        if actual < expected:
            return f"truncated: {actual:,} bytes, header implies {expected:,}"
        return None

    @staticmethod
    def Load(filename: str) -> MRCFile:
        """Read the header of an MRC file from disk and return an object for access"""

        mrc = open(filename, 'rb')

        # The mrc file header is always 1024, mostly empty space
        Header = mrc.read(MRCFile.HeaderLength)
        IsBigEndian = MRCFile.IsBigEndian(Header)
        obj = MRCFile(mrc, IsBigEndian)

        (obj.img_XDim, obj.img_YDim, obj.num_tiles, obj.img_pixel_mode) = struct.unpack(obj.EndianChar + 'IIII',
                                                                                        Header[0x00:0x10])

        (grid_XDim, grid_YDim, grid_ZDim) = struct.unpack(obj.EndianChar + 'III', Header[0x1C:0x28])
        obj.grid_dim = numpy.asarray((grid_XDim, grid_YDim, grid_ZDim), dtype=numpy.int64)

        (grid_cell_XDim, grid_cell_YDim, grid_cell_ZDim,) = struct.unpack(obj.EndianChar + 'fff', Header[0x28: 0x34])
        obj.grid_cell_dim = numpy.asarray((grid_cell_XDim, grid_cell_YDim, grid_cell_ZDim), dtype=numpy.float32)

        obj.pixel_spacing = obj.grid_cell_dim / obj.grid_dim.astype(numpy.float64)
        obj.pixel_spacing /= 10.0  # convert pixel spacing in nanometers

        (obj.mapx, obj.mapy, obj.mapz,) = struct.unpack(obj.EndianChar + 'III', Header[0x40: 0x4C])
        assert (obj.mapx == 1)  # If these asserts fail the image is stored in an untested orientation
        assert (obj.mapy == 2)
        assert (obj.mapz == 3)

        (obj.min_pixel_value, obj.max_pixel_value, obj.mean_pixel_value,) = struct.unpack(obj.EndianChar + 'fff',
                                                                                          Header[0x4C:0x58])

        (obj.extended_header_size,) = struct.unpack(obj.EndianChar + 'I', Header[0x5C: 0x60])

        (obj.tile_header_size, obj.tile_header_flags,) = struct.unpack(obj.EndianChar + 'HH', Header[0x80:0x84])

        (imod_stamp,) = struct.unpack(obj.EndianChar + 'I', Header[0x98: 0x9C])
        if imod_stamp == 1146047817:
            (obj.imod_flags,) = struct.unpack(obj.EndianChar + 'I', Header[0x9C: 0xA0])

        (img_origin_x, img_origin_y, img_origin_z,) = struct.unpack(obj.EndianChar + 'fff', Header[0xC4: 0xD0])
        obj.img_origin = numpy.asarray((img_origin_x, img_origin_y, img_origin_z), numpy.float64)

        for i in range(obj.num_tiles):  # type: ignore[arg-type]
            tile_meta = obj.ReadTileMeta(mrc, i)
            obj.tile_meta.append(tile_meta)

        return obj

    @property
    def EndianChar(self) -> Literal[">", "<"]:
        return '>' if self.IsBigEndian else '<'

    @property
    def bytes_per_pixel(self) -> int:
        if self.img_pixel_mode == 0:
            return 1
        elif self.img_pixel_mode == 1:
            return 2
        elif self.img_pixel_mode == 2:
            return 2
        elif self.img_pixel_mode == 3:
            return 4
        elif self.img_pixel_mode == 4:
            return 4
        elif self.img_pixel_mode == 6:
            return 2
        elif self.img_pixel_mode == 16:
            return 3
        else:
            raise ValueError("Unknown pixel format")

    @property
    def pixel_dtype(self) -> DTypeLike:
        """
        :return: The numpy dtype pixels are encoded in
        """
        if self.img_pixel_mode == 0:
            dtype = numpy.uint8
        elif self.img_pixel_mode == 1:
            dtype = numpy.int16
        elif self.img_pixel_mode == 2:
            dtype = numpy.float32
        elif self.img_pixel_mode == 3:
            dtype = numpy.dtype([('real', numpy.uint16), ('i', numpy.uint16)])
        elif self.img_pixel_mode == 4:
            dtype = numpy.complex128
        elif self.img_pixel_mode == 6:
            dtype = numpy.uint16
        elif self.img_pixel_mode == 16:
            dtype = numpy.dtype([('R', numpy.uint8), ('G', numpy.uint8), ('B', numpy.uint8)])
        else:
            raise ValueError("Unknown pixel format")

        sys_is_be = sys.byteorder == 'big'
        if sys_is_be != self.IsBigEndian:
            # OK, change the byte order of the numpy type
            dtype = numpy.dtype(dtype).newbyteorder(self.EndianChar)

        return dtype

    @property
    def pil_pixel_mode(self) -> str:
        """
        :return: The numpy dtype pixels are encoded in
        """
        if self.img_pixel_mode == 0:
            mode = 'L'
        elif self.img_pixel_mode == 1:
            mode = 'I;16'
        elif self.img_pixel_mode == 2:
            mode = 'F'
        elif self.img_pixel_mode == 3:
            raise ValueError("Complex valued pixels are not supported")
        elif self.img_pixel_mode == 4:
            raise ValueError("Complex valued pixels are not supported")
        elif self.img_pixel_mode == 6:
            mode = 'I;16'
        elif self.img_pixel_mode == 16:
            mode = 'RGB'
        else:
            raise ValueError("Unknown pixel format")

        sys_is_be = sys.byteorder == 'big'
        if sys_is_be != self.IsBigEndian:
            # OK, change the byte order of the numpy type
            # dtype = dtype.newbyteorder(self.EndianChar)
            Warning("Endian mismatch between operating system and mrc file encoding.  Check images for correctness.")

        return mode

    def __enter__(self):
        self._temp_dir = tempfile.mkdtemp()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        nornir_shared.files.rmtree(self._temp_dir)

    @property
    def image_length_in_bytes(self) -> int:
        """
        Return the number of bytes in a tile
        """
        return self.img_shape.prod() * self.bytes_per_pixel

    def _get_image_offset(self, iTile: int) -> int:
        """
        Return the offset to the first pixel of a tile
        """
        first_image_offset = MRCFile.HeaderLength + self.extended_header_size  # type: ignore[operator]
        image_byte_size = self.img_shape.prod() * self.bytes_per_pixel

        image_offset = first_image_offset + (image_byte_size * iTile)
        return image_offset

    def get_tile_as_bytes(self, iTile: int) -> bytes:
        """
        Return bytes
        """
        image_offset = self._get_image_offset(iTile)

        self.mrc.seek(image_offset)
        image_byte_length = self.image_length_in_bytes
        image_bytes = self.mrc.read(image_byte_length)
        while len(image_bytes) < image_byte_length:
            image_bytes = image_bytes + self.mrc.read(image_byte_length - len(image_bytes))

        return image_bytes

    def _repair_out_of_bounds_pixels(self, iTile: int, camera_bpp: int):
        """
        Used to repair old mrc files where the maximum pixel values were sometimes incorrect
        :param int camera_bpp: The maximum number of bits that could be encoded by the camera capturing the image
        """
        image_bytes = self.get_tile_as_bytes(iTile)
        img = numpy.frombuffer(image_bytes, dtype=self.pixel_dtype, count=self.img_shape.prod())
        max_val = (1 << camera_bpp) - 1
        outliers = img > max_val
        (iPixels,) = numpy.where(outliers)
        corrected = img[outliers] - max_val
        # imgb = numpy.copy(img)
        # imgb[outliers] = imgb[outliers] - max_val

        self.set_pixels(iTile, iPixels, new_values=corrected.tobytes(), old_values=img[outliers].tobytes())

    def set_pixels(self, iTile: int, iPixels: NDArray[np.integer], new_values: bytes, old_values: bytes | None = None):
        """
        :param int iTile: Index of tile to update
        :param list iPixels: Indicies of pixels to update
        :param bytes new_values: Values to set on the new pixels, must match length of iPixels * self.bytes_per_pixel
        :param bytes old_values: The current values at the pixels to be corrected.  Function will raise an exception if the expected value doesn't match.  Useful in debugging and development to ensure the correct pixels are being updated
        """

        tile_offset = self._get_image_offset(iTile)
        for (i, iPixel) in enumerate(iPixels):
            error_offset = tile_offset + (self.bytes_per_pixel * iPixel)
            iValueOffset = i * self.bytes_per_pixel

            if old_values is not None:
                self.mrc.seek(error_offset)
                current_val = self.mrc.read(self.bytes_per_pixel)

                expected_value = old_values[iValueOffset:iValueOffset + self.bytes_per_pixel]
                if current_val != expected_value:
                    raise Exception("Current value does not match the expected value, likely a bug")

            new_bytes = new_values[iValueOffset:iValueOffset + self.bytes_per_pixel]

            self.mrc.seek(error_offset)
            # self.mrc.write(new_bytes)

        return

    def get_tile_as_numpy(self, iTile: int) -> NDArray:
        """
        Return a numpy array
        """
        image_bytes = self.get_tile_as_bytes(iTile)
        img = numpy.frombuffer(image_bytes, dtype=self.pixel_dtype, count=self.img_shape.prod()).reshape(self.img_shape)
        return img

    def get_tile_as_numpy_memmap(self, iTile: int) -> NDArray:
        """
        Return a numpy memmap array
        """
        image_offset = self._get_image_offset(iTile)
        return np.memmap(filename=self.mrc, mode='c', dtype=self.pixel_dtype, shape=self.img_shape.prod(),
                         offset=image_offset).reshape(self.img_shape)

    def get_tile_as_image(self, iTile: int) -> PIL.Image.Image:
        """
        Return a pillow image
        """
        image_bytes = self.get_tile_as_bytes(iTile)
        im = PIL.Image.frombytes(data=image_bytes, mode=self.pil_pixel_mode,  # type: ignore[attr-defined]
                                 size=(self.img_shape[1], self.img_shape[0]))
        im = im.convert(mode='I')
        return im

    def ReadTileMeta(self, mrc, iTile: int) -> MRCTileHeader:
        mrc.seek(MRCFile.HeaderLength + (iTile * self.tile_header_size))  # type: ignore[operator]
        TileHeader = mrc.read(self.tile_header_size)
        while len(TileHeader) < self.tile_header_size:  # type: ignore[operator]
            TileHeader = TileHeader + mrc.read(self.tile_header_size - len(TileHeader))  # type: ignore[operator]

        return MRCTileHeader.Load(iTile, TileHeader,
                                  tile_flags=self.tile_header_flags,  # type: ignore[arg-type]
                                  nm_per_pixel=self.pixel_spacing[0:2],  # type: ignore[arg-type]
                                  big_endian=self.IsBigEndian)  # type: ignore[arg-type]

    @property
    def img_shape(self) -> NDArray[np.int64]:
        return numpy.asarray((self.img_XDim, self.img_YDim), dtype=numpy.int64)

    def __init__(self, mrc, isBigEndian: bool = False):
        self.mrc = mrc
        self.IsBigEndian = isBigEndian  # type: ignore[assignment]

        self.img_XDim = None
        self.img_YDim = None
        self.num_tiles = None
        self.img_pixel_mode = None
        self.grid_dim = None
        self.grid_cell_dim = None
        self.pixel_spacing = None  # Pixel spacing in nanometers

        self.min_pixel_value = None
        self.max_pixel_value = None
        self.mean_pixel_value = None

        self.extended_header_size = None  # Length of extended header, needed to find offset to first mrc image
        self.imod_flags = None

        self.img_origin = None

        self.tile_header_size = None
        self.tile_header_flags = None

        self.mapx = None
        self.mapy = None
        self.mapz = None

        self.tile_meta = []


class MRCTileHeaderFlags(enum.IntFlag):
    TiltAngle = 1
    PieceCoord = 2
    StageCoord = 4
    Magnification = 8
    Intensity = 16
    Exposure = 32


class MRCTileHeader:
    ID: int
    nm_per_pixel: float | NDArray[np.floating]
    tilt_angle: float | None
    piece_coords: NDArray[np.integer] | None
    stage_coords: NDArray[np.floating] | None
    mag: float | None
    intensity: float | None
    exposure: float | None

    _pixel_coords: NDArray[np.floating] | None = None  # Calculated value

    @staticmethod
    def calculate_pixel_coords(stage_coords: NDArray[np.floating], nm_per_pixel: float | NDArray[np.floating]) -> \
            NDArray[np.floating]:

        # stage_coords is in um, so convert to nm and then pixels
        nm_coord = stage_coords.astype(numpy.float64) * 1000.0  # Convert to nm
        pixel_coord = nm_coord / nm_per_pixel  # convert to pixels
        return pixel_coord

    @property
    def pixel_coords(self) -> NDArray[np.floating] | None:
        return self._pixel_coords

    @staticmethod
    def Load(tile_id: int, header, tile_flags: int, nm_per_pixel: float, big_endian: bool = False) -> MRCTileHeader:
        """
        :param tile_flags:
        :param nm_per_pixel:
        :param big_endian:
        :param tile_id: Arbitrary name for the tile we will load
        :param header: MRC File header
        """
        obj = MRCTileHeader(tile_id, nm_per_pixel)
        offset = 0

        Endian = '<'
        if big_endian:
            Endian = '>'

        # mrc_format.txt: "The short integers are signed, except for piece
        # coordinates." Parsing stage position as unsigned turns a negative
        # stage coordinate into ~+2621 px, scattering tiles that straddle the
        # stage origin. Sections whose coordinates share one sign survive it
        # because TranslateToZeroOrigin absorbs a uniform offset.
        if tile_flags & MRCTileHeaderFlags.TiltAngle:
            (obj.tilt_angle,) = struct.unpack(Endian + 'h', header[offset:offset + 2])
            obj.tilt_angle = float(obj.tilt_angle) / 100.0  # type: ignore[arg-type]
            offset += 2

        if tile_flags & MRCTileHeaderFlags.PieceCoord:
            # The documented exception: piece coordinates are unsigned.
            (px, py, pz,) = struct.unpack(Endian + 'HHH', header[offset:offset + 6])
            obj.piece_coords = numpy.asarray((px, py, pz))
            offset += 6

        if tile_flags & MRCTileHeaderFlags.StageCoord:
            (sx, sy,) = struct.unpack(Endian + 'hh', header[offset:offset + 4])
            obj.stage_coords = numpy.asarray((sx, sy), dtype=numpy.float32) / 25.0
            obj._pixel_coords = MRCTileHeader.calculate_pixel_coords(obj.stage_coords, nm_per_pixel)
            offset += 4

        if tile_flags & MRCTileHeaderFlags.Magnification:
            (obj.mag,) = struct.unpack(Endian + 'h', header[offset:offset + 2])
            obj.mag = float(obj.mag) * 100.0  # type: ignore[arg-type]
            offset += 2

        if tile_flags & MRCTileHeaderFlags.Intensity:
            (obj.intensity,) = struct.unpack(Endian + 'h', header[offset:offset + 2])
            obj.intensity = float(obj.intensity) / 25000.0  # type: ignore[arg-type]
            offset += 2

        if tile_flags & MRCTileHeaderFlags.Exposure:
            (obj.exposure,) = struct.unpack(Endian + 'f', header[offset:offset + 4])
            offset += 4

        return obj

    def __init__(self, ID: int, nm_per_pixel: float | NDArray[np.floating]):
        self.ID = ID
        self.nm_per_pixel = nm_per_pixel
        self.tilt_angle = None
        self.piece_coords = None
        self.stage_coords = None  # In Microns
        self.mag = None
        self.intensity = None
        self.exposure = None

        self._pixel_coords = None  # Calculated value

    def __str__(self, *args, **kwargs) -> str:
        output = str(self.ID)

        if self.stage_coords is not None:
            output += " Stage: {0},{1}".format(self.stage_coords[0], self.stage_coords[1])

        return output


if __name__ == '__main__':
    # obj = MRCFile.Load('C:/Data/RC1/0022_150X.mrc')
    __result = MRCFile.Load('C:/Data/RC1/0060.mrc')
