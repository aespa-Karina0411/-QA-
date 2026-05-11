from abc import ABC, abstractmethod


class CameraProvider(ABC):

    @abstractmethod
    def start(self) -> bool:
        ...

    @abstractmethod
    def read(self):
        ...

    @abstractmethod
    def stop(self):
        ...

    @abstractmethod
    def is_opened(self) -> bool:
        ...
