"""Python SDK for building parts in KOMPAS-3D v24 through its COM API."""

from .app import KompasApp, PartDocument
from .part import BOTH, NORMAL, REVERSE, SYMMETRIC, PartBuilder, Sketch, bolt_circle, mirror4

__all__ = [
    "KompasApp",
    "PartDocument",
    "PartBuilder",
    "Sketch",
    "bolt_circle",
    "mirror4",
    "NORMAL",
    "REVERSE",
    "BOTH",
    "SYMMETRIC",
]
__version__ = "0.1.0"
