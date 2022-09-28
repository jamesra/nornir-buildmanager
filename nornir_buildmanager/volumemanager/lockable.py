class Lockable(object):
    @property
    def Locked(self) -> bool:
        """
        Return true if the node is locked and should not be deleted
        """
        return bool(int(self.attrib.get('Locked', False)))

    @Locked.setter
    def Locked(self, value: bool | None):
        if value is None:
            if 'Locked' in self.attrib:
                del self.attrib['Locked']
            return

        assert (isinstance(value, bool))
        self.attrib['Locked'] = "%d" % value
