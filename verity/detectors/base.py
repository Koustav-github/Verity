from abc import ABC, abstractmethod

class Detector(ABC):
    """Base class for all detectors."""
    
    @abstractmethod
    def score(self, *args, **kwargs) -> float:
        """Score returns 0.0-1.0 (higher = better)"""
        pass
    
    @abstractmethod
    def get_name(self) -> str:
        """Return detector name."""
        pass