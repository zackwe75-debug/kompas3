"""Connection to a running (or newly started) KOMPAS-3D instance via COM."""

from __future__ import annotations

from pathlib import Path

from . import constants as c
from .part import PartBuilder


class KompasApp:
    """Thin wrapper over ``Kompas.Application.5``.

    ``kompas`` may be injected (e.g. a fake for tests); otherwise pywin32 is
    used to attach to KOMPAS-3D, starting it if needed.
    """

    def __init__(self, kompas=None, visible: bool = True):
        if kompas is None:
            try:
                from win32com.client import Dispatch
            except ImportError as exc:  # pragma: no cover - Windows only
                raise RuntimeError(
                    "pywin32 is required: pip install pywin32 (Windows with KOMPAS-3D v24)"
                ) from exc
            kompas = Dispatch("Kompas.Application.5")
        self.kompas = kompas
        self.kompas.Visible = visible

    def new_part(self, name: str | None = None, visible: bool = True) -> "PartDocument":
        doc = self.kompas.Document3D()
        # Create(invisible, typeDoc): typeDoc=True -> part (.m3d), False -> assembly
        if not doc.Create(not visible, True):
            raise RuntimeError("KOMPAS failed to create a part document")
        part = doc.GetPart(c.pTop_Part)
        if name:
            part.name = name
        return PartDocument(doc, part)


class PartDocument:
    def __init__(self, doc, part):
        self.doc = doc
        self.part = part
        self.builder = PartBuilder(part)

    def set_material(self, name: str, density: float) -> None:
        """Density in g/cm3."""
        self.part.SetMaterial(name, density)
        self.part.Update()

    def save(self, path: str | Path) -> Path:
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.doc.SaveAs(str(path)):
            raise RuntimeError(f"KOMPAS failed to save {path}")
        return path
