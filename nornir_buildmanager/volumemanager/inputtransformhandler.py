from nornir_buildmanager.volumemanager import ITransform
import nornir_shared.misc


class InputTransformHandler(object):
    """This can be added as a base class to another element.  It
       adds InputTransformChecksum and various option helper attributes.
    """

    def __init__(self, *args, **kwargs):
        super(InputTransformHandler, self).__init__(*args, **kwargs)

    @property
    def HasInputTransform(self) -> bool:
        return self.InputTransformChecksum is not None

    @property
    def InputTransform(self) -> str | None:
        return self.attrib.get('InputTransform', None)

    @InputTransform.setter
    def InputTransform(self, value):
        if value is None:
            if 'InputTransform' in self.attrib:
                del self.attrib['InputTransform']
        else:
            assert (isinstance(value, str))
            self.attrib['InputTransform'] = value

    @property
    def InputTransformChecksum(self) -> str | None:
        return self.attrib.get('InputTransformChecksum', None)

    @InputTransformChecksum.setter
    def InputTransformChecksum(self, value: str | None):
        if value is None:
            if 'InputTransformChecksum' in self.attrib:
                del self.attrib['InputTransformChecksum']
        else:
            self.attrib['InputTransformChecksum'] = value

    @property
    def InputTransformType(self) -> str | None:
        return self.attrib.get('InputTransformType', None)

    @InputTransformType.setter
    def InputTransformType(self, value: str | None):
        if value is None:
            if 'InputTransformType' in self.attrib:
                del self.attrib['InputTransformType']
        else:
            assert (isinstance(value, str))
            self.attrib['InputTransformType'] = value

    @property
    def InputTransformCropBox(self):
        """Returns boundaries of transform output if available, otherwise none
           :rtype tuple:
           :return (Xo, Yo, Width, Height):
        """

        if 'InputTransformCropBox' in self.attrib:
            return nornir_shared.misc.ListFromAttribute(self.attrib['InputTransformCropBox'])
        else:
            return None

    @InputTransformCropBox.setter
    def InputTransformCropBox(self, bounds: tuple[float] | None):
        """Sets boundaries in fixed space for output from the transform.
        :param bounds tuple:  (Xo, Yo, Width, Height) or (Width, Height)
        """
        if bounds is None:
            if 'InputTransformCropBox' in self.attrib:
                del self.attrib['InputTransformCropBox']
        elif len(bounds) == 4:
            self.attrib['InputTransformCropBox'] = ",".join([f'{v:g}' for v in bounds])
        elif len(bounds) == 2:
            self.attrib['InputTransformCropBox'] = "0,0," + ",".join([f'{v:g}' for v in bounds])
        else:
            raise Exception(
                "Invalid argument passed to InputTransformCropBox %s.  Expected 2 or 4 element tuple." % str(bounds))

    def SetTransform(self, transform_node: ITransform):
        if transform_node is None:
            self.InputTransformChecksum = None
            self.InputTransformType = None
            self.InputTransform = None
            self.InputTransformCropBox = None
        else:
            self.InputTransformChecksum = transform_node.Checksum
            self.InputTransformType = transform_node.Type
            self.InputTransform = transform_node.Name
            self.InputTransformCropBox = transform_node.CropBox

    def IsInputTransformMatched(self, transform_node: ITransform) -> bool:
        """Return true if the transform node matches our input transform"""
        if not self.HasInputTransform:
            return transform_node is None

        return self.InputTransform == transform_node.Name and \
            self.InputTransformType == transform_node.Type and \
            self.InputTransformChecksum == transform_node.Checksum and \
            self.InputTransformCropBox == transform_node.CropBox

    def CleanIfInputTransformMismatched(self, transform_node: ITransform) -> bool:
        """Remove this element from its parent if the transform node does not match our input transform attributes
        :return: True if element removed from parent, otherwise false
        :rtype: bool
        """
        if not self.IsInputTransformMatched(transform_node):
            self.Clean("Input transform %s did not match" % transform_node.FullPath)
            return True

        return False

    def InputTransformNeedsValidation(self) -> (bool, str):
        """
        :return: True if the MetaData indicates the input transform has changed
        and InputTransformIsValid should be called.
        """
        InputTransformType = self.attrib.get('InputTransformType', None)
        if InputTransformType is None:
            return False, "No input transform to validate"

        if self.InputTransformType is None:
            return False, "No input transform to validate"

        if len(self.InputTransformType) == 0:
            return False, "No input transform to validate"

        InputTransformNodes = self.FindAllFromParent("Transform[@Type='" + self.InputTransformType + "']")

        for it in InputTransformNodes:
            if it.NeedsValidation:
                return True, f"Potential Input Transform needs validation: {it.FullPath}"

        return False, "No Input Transforms found requiring validation"

    def InputTransformIsValid(self) -> (bool, str):
        """ Verify that the input transform matches the checksum recorded for the input. """

        InputTransformType = self.attrib.get('InputTransformType', None)
        if InputTransformType is None:
            return True, "No input transform"

        if len(self.InputTransformType) > 0:

            # Check all of our transforms with a matching type until we find a match that is valid
            nMatches = 0
            InputTransformNode = self.FindFromParent("Transform[@Type='" + self.InputTransformType + "']")

            # Due to an obnoxious mistake the StosGroupTransforms can have the same input type as the .mosaic files.
            # To work around this we check that the InputTransform is not ourselves
            if InputTransformNode == self:
                return True, "Could not validate input transform that was equal to self"

            # If we find a transform of the same type and no matching checksum the input transform is invalid
            # If there is no input transform we do not call it invalid since the input may exist in a different channel
            # If we find a transform of the same type and matching checksum the input transform is valid

            while InputTransformNode is not None:

                if InputTransformNode is None:
                    self.logger.warning(
                        'Expected input transform not found.  This can occur when the transform lives in a different channel.  Leaving node alone: ' + self.ToElementString())
                    return True, ""
                else:
                    nMatches += 1

                valid, reason = InputTransformNode.IsValid()
                if not valid:
                    return False, reason
                # else:
                #     if InputTransformNode.InputTransformType is not None:
                #         self.logger.debug('Check input transform of type: {0}'.format(self.InputTransformType))
                #         InputTransformNode = InputTransformNode.FindFromParent("Transform[@Type='" + InputTransformNode.InputTransformType + "']")
                #
                #         continue

                if self.IsInputTransformMatched(InputTransformNode):
                    return True, ""
                else:
                    return False, "Input Transform cleaned because of Input Transform mismatch"

            if nMatches > 0:  # If we had at least one hit then delete ourselves
                return False, 'Input Transform mismatch'
            else:
                return True, ""

        return True, ""

    @classmethod
    def EnumerateTransformDependents(cls, parent_node, checksum: str, type_name: str, recursive: bool):
        """Return a list of all sibling transforms (Same parent element) which have our checksum and type as an input transform checksum and type"""

        # WORKAROUND: The etree implementation has a serious shortcoming in that it cannot handle the 'and' operator in XPath queries.  This function is a workaround for a multiple criteria find query
        if parent_node is None:
            return

        for t in parent_node.findall('*'):
            if recursive:
                for c in cls.EnumerateTransformDependents(t, checksum, type_name, recursive):
                    yield c

            if 'InputTransformChecksum' not in t.attrib:
                continue

            if not t.InputTransformChecksum == checksum:
                continue

            if 'InputTransformType' in t.attrib:
                if not t.InputTransformType == type_name:
                    continue

            yield t

        return
