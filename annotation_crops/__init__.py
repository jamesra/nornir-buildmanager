"""AnnotationCrops file formats with no Nornir pipeline imports.

ignore.json, mask filenames, the training-loss table, and COCO RLE live here so
the gallery can import them without ``nornir_buildmanager``. The same sources are
vendored under ``nornir-buildmanager/annotation_crops`` so that package can import
them without a separate install. Keep the two trees identical.
"""

from annotation_crops.ignore import load_ignore_ids, save_ignore_ids
from annotation_crops.maskname import MaskName
from annotation_crops.rle import bbox_area_from_rle, decode_coco_rle, encode_coco_rle
from annotation_crops.scores import ensure_location_scores_schema

__all__ = [
    "MaskName",
    "bbox_area_from_rle",
    "decode_coco_rle",
    "encode_coco_rle",
    "ensure_location_scores_schema",
    "load_ignore_ids",
    "save_ignore_ids",
]
