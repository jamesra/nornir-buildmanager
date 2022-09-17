import abc
from abc import abstractmethod, abstractproperty


class IChecksum(abc.ABC):

    @abstractproperty
    def Checksum(self) -> str:
        raise NotImplementedError()


class ITransform(IChecksum, abc.ABC):
    """Interface to a meta-data element that provides a unique signature for a transform"""
    @abstractproperty
    def CropBox(self):
        raise NotImplementedError()

    @abstractproperty
    def Name(self) -> str:
        raise NotImplementedError()

    @abstractproperty
    def Type(self) -> str:
        raise NotImplementedError()