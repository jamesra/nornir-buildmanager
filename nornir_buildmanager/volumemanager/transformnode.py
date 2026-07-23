from __future__ import annotations

import math
import os

from nornir_buildmanager.volumemanager import ITransform, InputTransformHandler, MosaicBaseNode
from nornir_shared import prettyoutput as prettyoutput
import nornir_shared.misc


class TransformNode(MosaicBaseNode, InputTransformHandler, ITransform):

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Transform'

        self._validity_checked = None

        super(TransformNode, self).__init__(tag=tag, attrib=attrib, **extra)  # type: ignore[arg-type]

    @staticmethod
    def get_threshold_format() -> str:
        return f"%.{TransformNode.get_threshold_precision()}f"

    @staticmethod
    def get_threshold_precision() -> int:
        return 2  # Number of digits to save in XML file

    @staticmethod
    def round_precision_value(value) -> float:
        return float(TransformNode.get_threshold_format() % value)  # Number of digits to save in XML file

    @classmethod
    def Create(cls, Name: str, Type: str, Path: str | None = None, attrib: dict | None = None, **extra) -> TransformNode:

        if Path is None:
            Path = MosaicBaseNode.GetFilename(Name, Type)

        obj = cls(tag='Transform', Path=Path, Name=Name, Type=Type, attrib=attrib, **extra)
        return obj

    @property
    def TargetSectionNumber(self) -> int | None:
        return self.ControlSectionNumber

    @TargetSectionNumber.setter
    def TargetSectionNumber(self, value: int | None):
        self.ControlSectionNumber = value

    @property
    def ControlSectionNumber(self) -> int | None:
        if 'ControlSectionNumber' in self.attrib:
            return int(self.attrib['ControlSectionNumber'])

        return None

    @ControlSectionNumber.setter
    def ControlSectionNumber(self, value: int | None):

        if value is None:
            if 'ControlSectionNumber' in self.attrib:
                del self.attrib['ControlSectionNumber']
        else:
            self.attrib['ControlSectionNumber'] = "%d" % value

    @property
    def SourceSectionNumber(self) -> int | None:
        return self.MappedSectionNumber

    @SourceSectionNumber.setter
    def SourceSectionNumber(self, value: int | None):
        self.MappedSectionNumber = value

    @property
    def MappedSectionNumber(self) -> int | None:
        if 'MappedSectionNumber' in self.attrib:
            return int(self.attrib['MappedSectionNumber'])

        return None

    @MappedSectionNumber.setter
    def MappedSectionNumber(self, value: int | None):
        if value is None:
            if 'MappedSectionNumber' in self.attrib:
                del self.attrib['MappedSectionNumber']
        else:
            self.attrib['MappedSectionNumber'] = "%d" % value

    @property
    def Compressed(self) -> bool:
        """Indicates if the text representation of this transform has been
           compressed to save space.  Compressing is done by removing
           unnecessary precision in coordinates.  It is done to reduce
           the time required to parse the transform at load time.
           """
        value = self.attrib.get('Compressed', None)
        return bool(value) if value is not None else False

    @Compressed.setter
    def Compressed(self, value: bool | None):
        if value is None:
            if 'Compressed' in self.attrib:
                del self.attrib['Compressed']
        else:
            assert (isinstance(value, bool))
            self.attrib['Compressed'] = "%d" % value

    @property
    def min_blend(self) -> float | None:
        """Floor weight toward rigid linear blend used to create this transform."""
        value = self.attrib.get('min_blend', None)
        if value is None:
            legacy = self.attrib.get('linear_blend_factor', None)
            return float(legacy) if legacy is not None else None
        return float(value)

    @min_blend.setter
    def min_blend(self, value: float | None):
        if value is None:
            if 'min_blend' in self.attrib:
                del self.attrib['min_blend']
        else:
            self.attrib['min_blend'] = f'{value:g}'

    @property
    def linear_blend_factor(self) -> float:
        """Deprecated alias for min_blend; returns 0 when unset."""
        value = self.min_blend
        return value if value is not None else 0

    @linear_blend_factor.setter
    def linear_blend_factor(self, value: float | None):
        self.min_blend = value

    @property
    def max_blend(self) -> float | None:
        """Cap on per-point rigid blend weight used to create this transform."""
        value = self.attrib.get('max_blend', None)
        return float(value) if value is not None else None

    @max_blend.setter
    def max_blend(self, value: float | None):
        if value is None:
            if 'max_blend' in self.attrib:
                del self.attrib['max_blend']
        else:
            self.attrib['max_blend'] = f'{value:g}'

    @property
    def travel_limit(self) -> float | None:
        """Distance scale used for per-point linear blend when creating this transform."""
        value = self.attrib.get('travel_limit', None)
        return float(value) if value is not None else None

    @travel_limit.setter
    def travel_limit(self, value: float | None):
        if value is None:
            if 'travel_limit' in self.attrib:
                del self.attrib['travel_limit']
        else:
            self.attrib['travel_limit'] = f'{value:g}'

    @property
    def reblend_iterations(self) -> int | None:
        value = self.attrib.get('reblend_iterations', None)
        return int(value) if value is not None else None

    @reblend_iterations.setter
    def reblend_iterations(self, value: int | None):
        if value is None:
            if 'reblend_iterations' in self.attrib:
                del self.attrib['reblend_iterations']
        else:
            self.attrib['reblend_iterations'] = str(int(value))

    @property
    def reblend_tolerance(self) -> float | None:
        value = self.attrib.get('reblend_tolerance', None)
        return float(value) if value is not None else None

    @reblend_tolerance.setter
    def reblend_tolerance(self, value: float | None):
        if value is None:
            if 'reblend_tolerance' in self.attrib:
                del self.attrib['reblend_tolerance']
        else:
            self.attrib['reblend_tolerance'] = f'{value:g}'

    @property
    def chain_consistent_linear(self) -> bool:
        """True when slice-to-volume linear blend used a composed rigid slice-to-slice chain."""
        value = self.attrib.get('chain_consistent_linear', None)
        if value is None:
            return False
        return value in ('1', 'True', 'true')

    @chain_consistent_linear.setter
    def chain_consistent_linear(self, value: bool | None):
        if value is None:
            if 'chain_consistent_linear' in self.attrib:
                del self.attrib['chain_consistent_linear']
        else:
            self.attrib['chain_consistent_linear'] = '1' if value else '0'

    def SetLinearBlendParams(self,
                             min_blend: float | None,
                             travel_limit: float | None,
                             reblend_iterations: int | None,
                             reblend_tolerance: float | None,
                             max_blend: float | None = None,
                             chain_consistent_linear: bool | None = None,
                             *,
                             linear_blend_factor: float | None = None) -> None:
        """Persist linear-blend pipeline parameters on this transform node."""
        if linear_blend_factor is not None:
            if min_blend is not None and min_blend != linear_blend_factor:
                raise ValueError("min_blend and linear_blend_factor disagree")
            if min_blend is None:
                min_blend = linear_blend_factor
        self.min_blend = min_blend
        self.max_blend = max_blend
        self.travel_limit = travel_limit
        self.reblend_iterations = reblend_iterations
        self.reblend_tolerance = reblend_tolerance
        if chain_consistent_linear is not None:
            self.chain_consistent_linear = chain_consistent_linear

    def IsLinearBlendParamsMatched(self,
                                   min_blend: float | None,
                                   travel_limit: float | None,
                                   reblend_iterations: int | None,
                                   reblend_tolerance: float | None,
                                   max_blend: float | None = None,
                                   chain_consistent_linear: bool | None = None,
                                   *,
                                   linear_blend_factor: float | None = None) -> bool:
        """Return True if stored linear-blend parameters match the expected pipeline values."""
        if linear_blend_factor is not None:
            if min_blend is not None and min_blend != linear_blend_factor:
                return False
            if min_blend is None:
                min_blend = linear_blend_factor
        def _float_eq(stored: float | None, expected: float | None) -> bool:
            if stored is None and expected is None:
                return True
            if stored is None or expected is None:
                return False
            return abs(stored - expected) <= 1e-6

        stored_min_blend = self.min_blend if 'min_blend' in self.attrib or 'linear_blend_factor' in self.attrib else None
        matched = (_float_eq(self.travel_limit, travel_limit)
                   and _float_eq(self.reblend_tolerance, reblend_tolerance)
                   and self.reblend_iterations == reblend_iterations
                   and _float_eq(stored_min_blend, min_blend)
                   and _float_eq(self.max_blend if 'max_blend' in self.attrib else None, max_blend))
        if chain_consistent_linear is None:
            return matched
        return matched and self.chain_consistent_linear == chain_consistent_linear

    @property
    def CropBox(self):
        """Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        """
        value = self.attrib.get('CropBox', None)
        return nornir_shared.misc.ListFromAttribute(value) if value is not None else None

    def CropBoxDownsampled(self, downsample):
        (Xo, Yo, Width, Height) = self.CropBox  # type: ignore[misc]
        Xo //= float(downsample)
        Yo //= float(downsample)
        Width = int(math.ceil(Width / float(downsample)))
        Height = int(math.ceil(Height / float(downsample)))

        return Xo, Yo, Width, Height

    @CropBox.setter
    def CropBox(self, bounds):
        """Sets boundaries in fixed space for output from the transform.
        :param tuple bounds :  (Xo, Yo, Width, Height) or (Width, Height)
        """
        if len(bounds) == 4:
            self.attrib['CropBox'] = "%g,%g,%g,%g" % bounds
        elif len(bounds) == 2:
            self.attrib['CropBox'] = "0,0,%g,%g" % bounds
        elif bounds is None:
            if 'CropBox' in self.attrib:
                del self.attrib['CropBox']
        else:
            raise Exception(
                "Invalid argument passed to TransformNode.CropBox %s.  Expected 2 or 4 element tuple." % str(bounds))

    @property
    def NeedsValidation(self) -> bool:
        try:
            if self._validity_checked is True:
                return False
        except AttributeError:
            pass

        if super(TransformNode, self).NeedsValidation:
            return True

        input_needs_validation = InputTransformHandler.InputTransformNeedsValidation(self)
        return input_needs_validation[0]

    def IsValid(self) -> tuple[bool, str]:
        """Check if the transform is valid.  Be careful using this, because it only checks the existing meta-data.
           If you are comparing to a new input transform you should use VMH.IsInputTransformMatched"""

        # We write down the result the first time we check if the transform is valid since it is expensive
        try:
            if self._validity_checked is True:
                return True, "Validity already checked"
        except AttributeError:
            pass

        prettyoutput.Log('Validate: {0}'.format(self.FullPath))
        valid, reason = super(TransformNode, self).IsValid()
        if valid and not self.Locked:
            valid, reason = InputTransformHandler.InputTransformIsValid(self)

        # We can delete a locked transform if it does not exist on disk
        if not valid and not os.path.exists(self.FullPath):
            self.Locked = False
            return False, "Transform does not exist on disk"

        valid = valid or self.Locked

        if valid:
            self._validity_checked = valid

        return valid, reason if self.Locked is False else "Transform is locked"

    @property
    def Threshold(self) -> float | None:
        val = self.attrib.get('Threshold', None)
        try:
            return float(val)  # type: ignore[arg-type]
        except TypeError:  # val is None
            return None
        except ValueError:  # val is zero length string
            return None

    @Threshold.setter
    def Threshold(self, val: float | None):
        if val is None:
            if 'Threshold' in self.attrib:
                del self.attrib['Threshold']
        else:
            self.attrib['Threshold'] = TransformNode.get_threshold_format() % val
