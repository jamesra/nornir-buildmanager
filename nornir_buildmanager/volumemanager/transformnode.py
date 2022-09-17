from __future__ import annotations

import math
import os

import nornir_buildmanager
from nornir_buildmanager.volumemanager import ITransform, IChecksum, InputTransformHandler, MosaicBaseNode
import nornir_shared.misc
from nornir_shared import prettyoutput as prettyoutput


class TransformNode(MosaicBaseNode, InputTransformHandler, ITransform):

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Transform'

        self._validity_checked = None

        super(TransformNode, self).__init__(tag=tag, attrib=attrib, **extra)

    @staticmethod
    def get_threshold_format():
        return "%.2f"

    @staticmethod
    def get_threshold_precision():
        return 2  # Number of digits to save in XML file

    @staticmethod
    def round_precision_value(value):
        return float(TransformNode.get_threshold_format() % value)  # Number of digits to save in XML file

    @classmethod
    def Create(cls, Name: str, Type: str, Path: str = None, attrib: dict = None, **extra):

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
    def ControlSectionNumber(self, value):

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
    def MappedSectionNumber(self, value):
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
        if 'Compressed' in self.attrib:
            return bool(self.attrib['Compressed'])

        return False

    @Compressed.setter
    def Compressed(self, value):
        if value is None:
            if 'Compressed' in self.attrib:
                del self.attrib['Compressed']
        else:
            assert (isinstance(value, bool))
            self.attrib['Compressed'] = "%d" % value

    @property
    def CropBox(self):
        """Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        """

        if 'CropBox' in self.attrib:
            return nornir_shared.misc.ListFromAttribute(self.attrib['CropBox'])
        else:
            return None

    def CropBoxDownsampled(self, downsample):
        (Xo, Yo, Width, Height) = self.CropBox
        Xo = Xo // float(downsample)
        Yo = Yo // float(downsample)
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
        if valid:
            [valid, reason] = InputTransformHandler.InputTransformIsValid(self)

        # We can delete a locked transform if it does not exist on disk
        if not valid and not os.path.exists(self.FullPath):
            self.Locked = False

        if valid:
            self._validity_checked = valid

        return valid, reason

    @property
    def Threshold(self) -> float | None:
        val = self.attrib.get('Threshold', None)
        if isinstance(val, str):
            if len(val) == 0:
                return None

        if val is not None:
            val = float(val)

        return val

    @Threshold.setter
    def Threshold(self, val):
        if val is None and 'Threshold' in self.attrib:
            del self.attrib['Threshold']

        self.attrib['Threshold'] = TransformNode.get_threshold_format() % val
