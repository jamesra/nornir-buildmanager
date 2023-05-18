import abc
from abc import abstractmethod


class IChecksum(abc.ABC):

    @abstractmethod
    def Checksum(self) -> str:
        raise NotImplementedError()


class ITransform(IChecksum, abc.ABC):
    """Interface to a meta-data element that provides a unique signature for a transform"""

    @abstractmethod
    def CropBox(self):
        raise NotImplementedError()

    @abstractmethod
    def Name(self) -> str:
        raise NotImplementedError()

    @abstractmethod
    def Type(self) -> str:
        raise NotImplementedError()
