"""Connection to a running (or newly started) KOMPAS-3D instance via COM."""

from __future__ import annotations

from pathlib import Path

from . import constants as c
from .orientation import (
    AXES,
    CAL_D,
    CAL_OFFSET,
    CAL_T,
    CAL_U,
    CAL_V,
    Orientation,
    orientation_from_centroids,
)
from .part import SYMMETRIC, PartBuilder


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

    def new_part(self, name: str | None = None, visible: bool = True,
                 orientation: Orientation | None = None) -> "PartDocument":
        doc = self.kompas.Document3D()
        # Create(invisible, typeDoc): typeDoc=True -> part (.m3d), False -> assembly
        if not doc.Create(not visible, True):
            raise RuntimeError("KOMPAS failed to create a part document")
        part = doc.GetPart(c.pTop_Part)
        if name:
            part.name = name
        return PartDocument(doc, part, orientation)

    def calibrate(self) -> Orientation:
        """Measure sketch-axis directions of XY/XZ/YZ in this KOMPAS (hidden temp parts)."""
        result: Orientation = {}
        for plane in AXES:
            tmp = self.new_part(visible=False)
            try:
                pb = tmp.builder
                with pb.sketch(plane) as s:
                    s.circle((CAL_U, CAL_V), CAL_D)
                pb.extrude(s, CAL_T, SYMMETRIC)
                m1, c1 = tmp.mass_center()
                with pb.sketch(pb.offset_plane(plane, CAL_OFFSET, True)) as s:
                    s.circle((CAL_U, CAL_V), CAL_D)
                pb.extrude(s, 2 * CAL_OFFSET, SYMMETRIC)
                m2, c2 = tmp.mass_center()
                result[plane] = orientation_from_centroids(plane, c1, m1, c2, m2)
            finally:
                tmp.close()
        return result


class PartDocument:
    def __init__(self, doc, part, orientation: Orientation | None = None):
        self.doc = doc
        self.part = part
        self.builder = PartBuilder(part, orientation)

    def mass_center(self) -> tuple[float, tuple[float, float, float]]:
        """(mass kg, centre of mass mm)."""
        self.part.Update()
        mp = self.part.CalcMassInertiaProperties(c.ST_MIX_MM | c.ST_MIX_KG)
        return mp.m, (mp.xc, mp.yc, mp.zc)

    def close(self) -> None:
        self.doc.close()

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
