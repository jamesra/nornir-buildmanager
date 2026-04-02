class Lockable(object):
    @property
    def Locked(self) -> bool:
        """
        Return true if the node is locked and should not be deleted
        """
        return bool(int(self.attrib.get('Locked', False)))  # type: ignore[attr-defined]


    @Locked.setter
    def Locked(self, value: bool | None):
        attrib = self.attrib  # type: ignore[attr-defined]
        if value is None:
            if 'Locked' in attrib:
                del attrib['Locked']
            return

        assert (isinstance(value, bool))
        attrib['Locked'] = "%d" % value
