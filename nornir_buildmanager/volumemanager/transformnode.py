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

        super(TransformNode, self).__init__(tag=tag, attrib=attrib, **extra)

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
    def Create(cls, Name: str, Type: str, Path: str = None, attrib: dict = None, **extra) -> TransformNode:

        if Path is None:
            Path = MosaicBaseNode.GetFilename(Name, Type)

        obj = cls(tag='Transform', Path=Path, Name=Name, Type=Type, attrib=attrib, **extra)
        return obj

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
    def linear_blend_factor(self) -> float:
        """If not None, the amount of linear blend used to create this transform.
        If no value is stored, the linear blend amount is zero"""
        value = self.attrib.get('linear_blend_factor', None)
        return float(value) if value is not None else 0

    @linear_blend_factor.setter
    def linear_blend_factor(self, value: float | None):
        if value is None or value == 0:
            if 'linear_blend_factor' in self.attrib:
                del self.attrib['linear_blend_factor']
        else:
            self.attrib['linear_blend_factor'] = f'{value:3F}'

    @property
    def CropBox(self):
        """Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        """
        value = self.attrib.get('CropBox', None)
        return nornir_shared.misc.ListFromAttribute(value) if value is not None else None

    def CropBoxDownsampled(self, downsample):
        (Xo, Yo, Width, Height) = self.CropBox
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
    def NeedsValidation(self) -> (bool, str):
        try:
            if self._validity_checked is True:
                return False, "Validity already checked"
        except AttributeError:
            pass

        if super(TransformNode, self).NeedsValidation:
            return True, ""

        input_needs_validation = InputTransformHandler.InputTransformNeedsValidation(self)
        return input_needs_validation

    def IsValid(self) -> (bool, str):
        """Check if the transform is valid.  Be careful using this, because it only checks the existing meta-data.
           If you are comparing to a new input transform you should use VMH.IsInputTransformMatched"""

        # We write down the result the first time we check if the transform is valid since it is expensive
        try:
            if self._validity_checked is True:
                return True, "Validity already checked"
        except AttributeError:
            pass

        [valid, reason] = super(TransformNode, self).IsValid()
        prettyoutput.Log('Validate: {0}'.format(self.FullPath))
        if valid and not self.Locked:
            [valid, reason] = InputTransformHandler.InputTransformIsValid(self)

        # We can delete a locked transform if it does not exist on disk
        if not valid and not os.path.exists(self.FullPath):
            self.Locked = False

        valid = valid or self.Locked

        if valid:
            self._validity_checked = valid

        return valid, reason if self.Locked is False else "Transform is locked"

    @property
    def Threshold(self) -> float | None:
        val = self.attrib.get('Threshold', None)
        try:
            return float(val)
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
