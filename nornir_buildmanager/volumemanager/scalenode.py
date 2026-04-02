from __future__ import annotations

from xml.etree import ElementTree as ElementTree

from nornir_buildmanager.volumemanager import XElementWrapper


class ScaleNode(XElementWrapper):

    @property
    def X(self) -> ScaleAxis | None:
        x_elem = ElementTree.Element.find(self,
                                          'X')  # Bypass the extra cruft in XElementTree since scale uses XML with no link loading or special wrapping of elements

        if x_elem is None:
            return None

        return ScaleAxis(float(x_elem.attrib['UnitsPerPixel']), x_elem.attrib['UnitsOfMeasure'])

    @property
    def Y(self) -> ScaleAxis | None:
        y_elem = ElementTree.Element.find(self,
                                          'Y')  # Bypass the extra cruft in XElementTree since scale uses XML with no link loading or special wrapping of elements

        if y_elem is None:
            return None

        return ScaleAxis(float(y_elem.attrib['UnitsPerPixel']), y_elem.attrib['UnitsOfMeasure'])

    @property
    def Z(self) -> ScaleAxis | None:
        z_elem = ElementTree.Element.find(self,
                                          'Z')  # Bypass the extra cruft in XElementTree since scale uses XML with no link loading or special wrapping of elements

        if z_elem is None:
            return None

        return ScaleAxis(float(z_elem.attrib['UnitsPerPixel']), z_elem.attrib['UnitsOfMeasure'])

    def __init__(self, tag=None, attrib=None, **extra):
        if tag is None:
            tag = 'Scale'

        super(ScaleNode, self).__init__(tag=tag, attrib=attrib, **extra)  # type: ignore[arg-type]

    def __str__(self):
        if self.Z is not None:
            return "X:{0} Y:{1} Z:{2}".format(str(self.X), str(self.Y), str(self.Z))
        else:
            return "X:{0} Y:{1}".format(str(self.X), str(self.Y))

    @classmethod
    def Create(cls, **extra) -> ScaleNode:
        return cls(**extra)

    @classmethod
    def CreateFromScale(cls, scale: Scale) -> ScaleNode:
        """Create a ScaleNode from a Scale object"""
        if isinstance(scale, Scale) is False:
            raise NotImplementedError('CreateFromScale got unexpected parameter: %s' % str(scale))

        output = ScaleNode()
        assert scale.X is not None and scale.Y is not None
        output.UpdateOrAddChild(XElementWrapper('X', {'UnitsOfMeasure': scale.X.UnitsOfMeasure,
                                                      'UnitsPerPixel': str(scale.X.UnitsPerPixel)}))
        output.UpdateOrAddChild(XElementWrapper('Y', {'UnitsOfMeasure': scale.Y.UnitsOfMeasure,
                                                      'UnitsPerPixel': str(scale.Y.UnitsPerPixel)}))

        if scale.Z is not None:
            output.UpdateOrAddChild(XElementWrapper('Z', {'UnitsOfMeasure': scale.Z.UnitsOfMeasure,
                                                          'UnitsPerPixel': str(scale.Z.UnitsPerPixel)}))

        return output


class Scale(object):
    """
    A 2/3 dimensional representation of scale.
    Units should be in nanometers
    """
    _x: ScaleAxis | None
    _y: ScaleAxis | None
    _z: ScaleAxis | None

    @property
    def X(self) -> ScaleAxis | None:
        return self._x

    @X.setter
    def X(self, val: float | ScaleAxis | None):
        if val is None:
            self._x = None
        elif isinstance(val, float):
            self._x = ScaleAxis(val, 'nm')
        elif isinstance(val, ScaleAxis):
            self._x = val
        else:
            raise NotImplementedError('Unknown type passed to Scale setter %s' % val)

    @property
    def Y(self) -> ScaleAxis | None:
        return self._y

    @Y.setter
    def Y(self, val: float | ScaleAxis | None):
        if val is None:
            self._y = None
        elif isinstance(val, float):
            self._y = ScaleAxis(val, 'nm')
        elif isinstance(val, ScaleAxis):
            self._y = val
        else:
            raise NotImplementedError('Unknown type passed to Scale setter %s' % val)

    @property
    def Z(self) -> ScaleAxis | None:
        return self._z

    @Z.setter
    def Z(self, val: float | ScaleAxis | None):
        if val is None:
            self._z = None
        elif isinstance(val, float):
            self._z = ScaleAxis(val, 'nm')
        elif isinstance(val, ScaleAxis):
            self._z = val
        else:
            raise NotImplementedError('Unknown type passed to Scale setter %s' % val)

    @staticmethod
    def Create(ScaleData):
        """Create a Scale object from various input types"""
        if isinstance(ScaleData, ScaleNode):
            obj = Scale(ScaleData.X, ScaleData.Y, ScaleData.Z)  # type: ignore[arg-type]
            return obj
        else:
            raise NotImplementedError("Unexpected type passed to Scale.Create %s" % ScaleData)

    def __truediv__(self, scalar):
        if not isinstance(scalar, float):
            raise NotImplementedError("Division for non-floating types is not supported")

        assert self.X is not None and self.Y is not None
        obj = Scale(self.X / scalar,
                    self.Y / scalar,
                    self.Z / scalar if self.Z is not None else None)  # Only pass Z if it is not None
        return obj

    def __mul__(self, scalar):
        if not isinstance(scalar, float):
            raise NotImplementedError("Division for non-floating types is not supported")

        assert self.X is not None and self.Y is not None
        obj = Scale(self.X * scalar,
                    self.Y * scalar,
                    self.Z * scalar if self.Z is not None else None)  # Only pass Z if it is not None
        return obj

    def __init__(self,
                 X: float | ScaleAxis | None,
                 Y: float | ScaleAxis | None = None,
                 Z: float | ScaleAxis | None = None):
        """
        If only X is passed we assume the scale for X&Y are identical and there is no Z
        The only way to specify a Z axis scale is to pass it to the constructor
        """
        self._x = None
        self._y = None
        self._z = None

        self.X = X

        if Y is None:
            self.Y = X
        else:
            self.Y = Y

        if Z is not None:
            self.Z = Z

    def __str__(self):
        if self.Z is not None:
            return "X:{0} Y:{1} Z:{2}".format(str(self.X), str(self.Y), str(self.Z))
        else:
            return "X:{0} Y:{1}".format(str(self.X), str(self.Y))


class ScaleAxis:
    UnitsPerPixel: float
    UnitsOfMeasure: str

    def __init__(self, UnitsPerPixel: float, UnitsOfMeasure: str):
        self.UnitsPerPixel = float(UnitsPerPixel)
        self.UnitsOfMeasure = str(UnitsOfMeasure)

    def __truediv__(self, scalar: float):
        if isinstance(scalar, float):
            return ScaleAxis(self.UnitsPerPixel / scalar, self.UnitsOfMeasure)
        elif isinstance(scalar, ScaleAxis):
            if self.UnitsOfMeasure != scalar.UnitsOfMeasure:
                raise NotImplementedError("Cannot divide ScaleAxis objects if UnitsOfMeasure do not match")

            return self.UnitsPerPixel / scalar.UnitsPerPixel  # both inputs have units so the units cancel

        raise NotImplementedError("Division for input type is not supported: %s" % scalar)

    def __eq__(self, other) -> bool:
        if other is None:
            return False

        return other.UnitsPerPixel == self.UnitsPerPixel and other.UnitsOfMeasure == self.UnitsOfMeasure

    def __mul__(self, scalar: float):
        if isinstance(scalar, float):
            return ScaleAxis(self.UnitsPerPixel * scalar, self.UnitsOfMeasure)
        elif isinstance(scalar, ScaleAxis):
            if self.UnitsOfMeasure != scalar.UnitsOfMeasure:
                raise NotImplementedError("Cannot multiply ScaleAxis objects if UnitsOfMeasure do not match")

            return self.UnitsPerPixel * scalar.UnitsPerPixel  # both inputs have units so the units cancel

        raise NotImplementedError("Multiplication for input type is not supported: %s" % scalar)

    def __str__(self):
        return "{0}{1}".format(str(self.UnitsPerPixel), self.UnitsOfMeasure)
