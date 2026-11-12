"""KOMPAS-3D v24: build the «vtulka» part (single-file version of kompas_sdk).

Usage (Windows, KOMPAS-3D v24, 64-bit Python):
    pip install pywin32
    python kompas_vtulka.py --out vtulka.m3d
Options: see --help
"""
from __future__ import annotations

import argparse
import math
from contextlib import contextmanager
from dataclasses import dataclass, field  # noqa: F401
from pathlib import Path
from typing import Callable, Iterable, Iterator

class c:  # KOMPAS API5 constants

    # Part selector
    pTop_Part = -1

    # ksObj3dTypeEnum
    o3d_planeXOY = 1
    o3d_planeXOZ = 2
    o3d_planeYOZ = 3
    o3d_sketch = 5
    o3d_edge = 7
    o3d_planeOffset = 14
    o3d_baseExtrusion = 24
    o3d_bossExtrusion = 25
    o3d_cutExtrusion = 26
    o3d_baseRotated = 27
    o3d_bossRotated = 28
    o3d_cutRotated = 29
    o3d_fillet = 34

    # ksDirectionTypeEnum
    dtNormal = 0
    dtReverse = 1
    dtBoth = 2
    dtMiddlePlane = 3

    # ksEndTypeEnum
    etBlind = 0
    etThroughAll = 1

    # Line style for sketch geometry: 1 = main (contour) line
    STYLE_MAIN = 1
    STYLE_AXIS = 3  # axial line; used as the revolve axis

    # CalcMassInertiaProperties units (ldefin2d.h)
    ST_MIX_MM = 0x1
    ST_MIX_KG = 0x10

# ---- orientation ------------------------------------------------------------



AXES = {"XY": ("X", "Y", "Z"), "XZ": ("X", "Z", "Y"), "YZ": ("Z", "Y", "X")}
_IDX = {"X": 0, "Y": 1, "Z": 2}

Vec = tuple[float, float, float]


@dataclass(frozen=True)
class PlaneOrientation:
    u: tuple[str, int]  # world axis and sign of the sketch's local x
    v: tuple[str, int]  # world axis and sign of the sketch's local y
    offset_sign: int  # +1 if an offset plane with direction=True lies on the +normal side

    def to_local(self, plane: str, a: float, b: float) -> tuple[float, float]:
        ax, bx, _ = AXES[plane]
        world = {ax: a, bx: b}
        return self.u[1] * world.get(self.u[0], 0.0), self.v[1] * world.get(self.v[0], 0.0)

    def flips_handedness(self, plane: str) -> bool:
        ax, bx, _ = AXES[plane]
        # Jacobian of (a, b) -> (u, v); negative determinant mirrors arc direction
        du = (self.u[1] if self.u[0] == ax else 0, self.u[1] if self.u[0] == bx else 0)
        dv = (self.v[1] if self.v[0] == ax else 0, self.v[1] if self.v[0] == bx else 0)
        return du[0] * dv[1] - du[1] * dv[0] < 0


Orientation = dict[str, PlaneOrientation]

# Used only if calibration is skipped. XY and XZ match published measurements;
# YZ is a guess.
FALLBACK: Orientation = {
    "XY": PlaneOrientation(("X", 1), ("Y", 1), 1),
    "XZ": PlaneOrientation(("X", 1), ("Z", -1), 1),
    "YZ": PlaneOrientation(("Z", -1), ("Y", 1), 1),
}

# Calibration probe: a circle at local (CAL_U, CAL_V) extruded symmetrically on
# the plane, then the same circle on a plane offset by CAL_OFFSET, extruded
# 2*CAL_OFFSET symmetrically (so it overlaps the first body).
CAL_U, CAL_V, CAL_OFFSET, CAL_D, CAL_T = 30.0, 50.0, 20.0, 12.0, 4.0


def _axis_with(vec: Vec, magnitude: float, tol: float) -> tuple[str, int]:
    hits = [(name, 1 if vec[i] > 0 else -1) for name, i in _IDX.items()
            if abs(abs(vec[i]) - magnitude) <= tol]
    if len(hits) != 1:
        raise RuntimeError(f"calibration: no unique axis with |{magnitude}| in {vec}")
    return hits[0]


def orientation_from_centroids(plane: str, c1: Vec, m1: float, c2: Vec, m2: float,
                               tol: float = 1.0) -> PlaneOrientation:
    """c1/m1: probe on the base plane; c2/m2: totals after the offset probe."""
    u = _axis_with(c1, CAL_U, tol)
    v = _axis_with(c1, CAL_V, tol)
    _, _, normal = AXES[plane]
    if {u[0], v[0]} != set(AXES[plane][:2]):
        raise RuntimeError(f"calibration: sketch axes {u}, {v} not in plane {plane}")
    added = tuple((m2 * b - m1 * a) / (m2 - m1) for a, b in zip(c1, c2))
    if (_axis_with(added, CAL_U, tol), _axis_with(added, CAL_V, tol)) != (u, v):
        raise RuntimeError("calibration: offset plane sketch axes differ from base plane")
    # added body spans offsets CAL_T/2 .. 2*CAL_OFFSET -> centroid ~ +-(CAL_OFFSET + CAL_T/4)
    n = added[_IDX[normal]]
    if abs(abs(n) - (CAL_OFFSET + CAL_T / 4)) > tol:
        raise RuntimeError(f"calibration: unexpected offset centroid {n}")
    return PlaneOrientation(u, v, 1 if n > 0 else -1)

# ---- part ------------------------------------------------------------




Point = tuple[float, float]

# Extrusion modes
NORMAL = "normal"  # along plane normal
REVERSE = "reverse"  # against plane normal
BOTH = "both"  # `depth` to each side of the plane
SYMMETRIC = "symmetric"  # `depth` total, centred on the plane

_DIRECTIONS = {
    NORMAL: c.dtNormal,
    REVERSE: c.dtReverse,
    BOTH: c.dtBoth,
    SYMMETRIC: c.dtMiddlePlane,
}

_DEFAULT_PLANES = {"XY": c.o3d_planeXOY, "XZ": c.o3d_planeXOZ, "YZ": c.o3d_planeYOZ}


class Sketch:
    """2D drawing helpers bound to an open sketch (``ksDocument2D``)."""

    def __init__(self, entity, doc2d, to_local: Callable[[float, float], Point] | None = None,
                 mirrored: bool = False):
        self.entity = entity
        self.doc2d = doc2d
        # World-sketch mode: input points are converted to KOMPAS sketch coordinates
        self._m = to_local or (lambda a, b: (a, b))
        self._mirrored = mirrored

    def line(self, p1: Point, p2: Point, style: int = c.STYLE_MAIN):
        (x1, y1), (x2, y2) = self._m(*p1), self._m(*p2)
        return self.doc2d.ksLineSeg(x1, y1, x2, y2, style)

    def axis(self, p1: Point, p2: Point):
        """Axial line; a revolve uses it as the rotation axis."""
        return self.line(p1, p2, c.STYLE_AXIS)

    def circle(self, center: Point, diameter: float):
        x, y = self._m(*center)
        return self.doc2d.ksCircle(x, y, diameter / 2.0, c.STYLE_MAIN)

    def circles(self, centers: Iterable[Point], diameter: float) -> None:
        for p in centers:
            self.circle(p, diameter)

    def arc(self, center: Point, p1: Point, p2: Point, ccw: bool = True):
        r = math.dist(center, p1)
        (cx, cy), (x1, y1), (x2, y2) = self._m(*center), self._m(*p1), self._m(*p2)
        direction = 1 if ccw != self._mirrored else -1
        return self.doc2d.ksArcByPoint(cx, cy, r, x1, y1, x2, y2, direction, c.STYLE_MAIN)

    def box(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Axis-aligned rectangle by two corners."""
        self.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

    def obround(self, center: Point, length: float, width: float, vertical: bool = False) -> None:
        """Slot with semicircular ends; ``length`` is overall, along X (or Y if vertical)."""
        cx, cy = center
        r, h = width / 2.0, length / 2.0 - width / 2.0
        if h <= 0:
            raise ValueError("obround length must exceed width")
        if vertical:
            top, bot = (cx, cy + h), (cx, cy - h)
            self.line((cx + r, cy - h), (cx + r, cy + h))
            self.arc(top, (cx + r, cy + h), (cx - r, cy + h), ccw=True)
            self.line((cx - r, cy + h), (cx - r, cy - h))
            self.arc(bot, (cx - r, cy - h), (cx + r, cy - h), ccw=True)
        else:
            right, left = (cx + h, cy), (cx - h, cy)
            self.line((cx - h, cy - r), (cx + h, cy - r))
            self.arc(right, (cx + h, cy - r), (cx + h, cy + r), ccw=True)
            self.line((cx + h, cy + r), (cx - h, cy + r))
            self.arc(left, (cx - h, cy + r), (cx - h, cy - r), ccw=True)

    def polyline(self, points: list[Point], closed: bool = True) -> None:
        pts = points + [points[0]] if closed else points
        for a, b in zip(pts, pts[1:]):
            self.line(a, b)

    def rect(self, center: Point, width: float, height: float) -> None:
        cx, cy = center
        w, h = width / 2.0, height / 2.0
        self.polyline([(cx - w, cy - h), (cx + w, cy - h), (cx + w, cy + h), (cx - w, cy + h)])

    def rects(self, centers: Iterable[Point], width: float, height: float) -> None:
        for p in centers:
            self.rect(p, width, height)


class PartBuilder:
    """Creates planes, sketches, extrusions and cuts on a ``ksPart``.

    ``sketch`` uses raw KOMPAS sketch coordinates (orientation-dependent).
    ``world_sketch``/``prism``/``through`` take world coordinates and need an
    ``orientation`` (from ``KompasApp.calibrate``); see ``orientation.AXES``.
    """

    def __init__(self, part, orientation: Orientation | None = None):
        self.part = part
        self.orientation = orientation
        self._has_body = False
        self._planes: dict[tuple[str, float], object] = {}
        self.features: list[tuple[str, object]] = []

    # --- planes -----------------------------------------------------------
    def plane(self, name: str):
        return self.part.GetDefaultEntity(_DEFAULT_PLANES[name.upper()])

    def offset_plane(self, base, offset: float, positive: bool = True):
        """Plane parallel to ``base`` (entity or "XY"/"XZ"/"YZ") at ``offset`` mm."""
        if isinstance(base, str):
            base = self.plane(base)
        entity = self.part.NewEntity(c.o3d_planeOffset)
        d = entity.GetDefinition()
        d.SetPlane(base)
        d.offset = offset
        d.direction = positive
        entity.Create()
        self.features.append(("plane", entity))
        return entity

    def offset_plane_pair(self, base, offset: float):
        """Two planes at +offset and -offset from ``base``."""
        return self.offset_plane(base, offset, True), self.offset_plane(base, offset, False)

    # --- sketches ---------------------------------------------------------
    @contextmanager
    def sketch(self, plane) -> Iterator[Sketch]:
        if isinstance(plane, str):
            plane = self.plane(plane)
        entity = self.part.NewEntity(c.o3d_sketch)
        d = entity.GetDefinition()
        d.SetPlane(plane)
        entity.Create()
        doc2d = d.BeginEdit()
        try:
            yield Sketch(entity, doc2d)
        finally:
            d.EndEdit()
        self.features.append(("sketch", entity))

    # --- world-coordinate API ---------------------------------------------
    def _world_plane(self, plane: str, at: float):
        """Default plane, or plane parallel to it at world coordinate ``at`` on its normal."""
        if abs(at) < 1e-9:
            return self.plane(plane)
        key = (plane, round(at, 6))
        if key not in self._planes:
            positive = (at > 0) == (self.orientation[plane].offset_sign > 0)
            self._planes[key] = self.offset_plane(plane, abs(at), positive)
        return self._planes[key]

    @contextmanager
    def world_sketch(self, plane: str, at: float = 0.0) -> Iterator[Sketch]:
        """Sketch in world coordinates (see ``orientation.AXES``) at ``at`` along the normal."""
        if self.orientation is None:
            raise RuntimeError("world_sketch needs an orientation; call KompasApp.calibrate()")
        plane = plane.upper()
        o = self.orientation[plane]
        with self.sketch(self._world_plane(plane, at)) as s:
            s._m = lambda a, b: o.to_local(plane, a, b)
            s._mirrored = o.flips_handedness(plane)
            yield s

    def prism(self, plane: str, n0: float, n1: float, draw: Callable[[Sketch], None],
              cut: bool = False):
        """Extrude (or cut) the profile from world ``n0`` to ``n1`` along the plane normal."""
        lo, hi = sorted((n0, n1))
        if hi - lo <= 0:
            raise ValueError("prism needs n0 != n1")
        with self.world_sketch(plane, (lo + hi) / 2) as s:
            draw(s)
        op = self.cut if cut else self.extrude
        return op(s, hi - lo, SYMMETRIC)

    def through(self, plane: str, draw: Callable[[Sketch], None]):
        """Cut the profile through the whole part along the plane normal."""
        with self.world_sketch(plane) as s:
            draw(s)
        return self.cut(s, mode=BOTH, through=True)

    def revolve(self, plane: str, profile: list[Point], axis: tuple[Point, Point],
                cut: bool = False, angle: float = 360.0):
        """Revolve a closed world-coordinate profile about ``axis`` (both in ``plane``)."""
        with self.world_sketch(plane) as s:
            s.polyline(profile)
            s.axis(*axis)
        if cut:
            if not self._has_body:
                raise RuntimeError("cut requires an existing body")
            kind = c.o3d_cutRotated
        else:
            kind = c.o3d_bossRotated if self._has_body else c.o3d_baseRotated
        entity = self.part.NewEntity(kind)
        d = entity.GetDefinition()
        if cut:
            d.cut = True
        d.directionType = c.dtNormal
        d.SetSideParam(True, angle)
        d.SetSketch(s.entity)
        if not entity.Create():
            raise RuntimeError(f"KOMPAS failed to create revolve feature (type {kind})")
        self._has_body = self._has_body or not cut
        self.features.append(("cut" if cut else "revolve", entity))
        return entity

    # --- solid features ---------------------------------------------------
    def extrude(self, sketch: Sketch, depth: float, mode: str = NORMAL, through: bool = False):
        """Add material. The first extrusion becomes the base body."""
        kind = c.o3d_bossExtrusion if self._has_body else c.o3d_baseExtrusion
        entity = self._extrusion(kind, sketch, depth, mode, through)
        self._has_body = True
        self.features.append(("extrude", entity))
        return entity

    def cut(self, sketch: Sketch, depth: float = 0.0, mode: str = NORMAL, through: bool = False):
        """Remove material. ``through=True`` cuts through all (``depth`` ignored)."""
        if not self._has_body:
            raise RuntimeError("cut requires an existing body")
        entity = self._extrusion(c.o3d_cutExtrusion, sketch, depth, mode, through)
        self.features.append(("cut", entity))
        return entity

    def _extrusion(self, kind, sketch: Sketch, depth, mode, through):
        if mode not in _DIRECTIONS:
            raise ValueError(f"unknown mode {mode!r}")
        if not through and depth <= 0:
            raise ValueError("depth must be positive")
        entity = self.part.NewEntity(kind)
        d = entity.GetDefinition()
        if kind == c.o3d_cutExtrusion:
            d.cut = True
        d.directionType = _DIRECTIONS[mode]
        end = c.etThroughAll if through else c.etBlind
        # SetSideParam(side1, endType, depth, draftAngle, draftOutward)
        if mode == BOTH:
            d.SetSideParam(True, end, depth, 0, False)
            d.SetSideParam(False, end, depth, 0, False)
        else:
            d.SetSideParam(mode != REVERSE, end, depth, 0, False)
        if kind != c.o3d_cutExtrusion:
            d.SetThinParam(False, 0, 0, 0)
        d.SetSketch(sketch.entity)
        if not entity.Create():
            raise RuntimeError(f"KOMPAS failed to create extrusion feature (type {kind})")
        return entity

    def fillet_all_edges(self, radius: float) -> bool:
        """Round every edge of the current body in one fillet feature.

        Returns False (instead of raising) when KOMPAS cannot build it, so a
        cosmetic fillet never aborts the whole model.
        """
        if radius <= 0:
            raise ValueError("radius must be positive")
        edges = self.part.EntityCollection(c.o3d_edge)
        count = edges.GetCount()
        if not count:
            return False
        entity = self.part.NewEntity(c.o3d_fillet)
        d = entity.GetDefinition()
        d.radius = radius
        d.tangent = True
        arr = d.array()
        for i in range(count):
            arr.Add(edges.GetByIndex(i))
        if not entity.Create():
            return False
        self.features.append(("fillet", entity))
        return True

    def update(self) -> None:
        self.part.Update()


def bolt_circle(diameter: float, count: int, start_deg: float = 0.0) -> list[Point]:
    """Hole centres evenly spaced on a pitch circle."""
    r = diameter / 2.0
    return [
        (r * math.cos(math.radians(start_deg + i * 360.0 / count)),
         r * math.sin(math.radians(start_deg + i * 360.0 / count)))
        for i in range(count)
    ]


def symmetric_circle(diameter: float, quadrant_deg: Iterable[float]) -> list[Point]:
    """Hole centres at the given angles (0–90°, from the X axis) mirrored into all four quadrants."""
    r = diameter / 2.0
    pts: list[Point] = []
    for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
        for a in quadrant_deg:
            pts.append((sx * r * math.cos(math.radians(a)), sy * r * math.sin(math.radians(a))))
    return pts


def mirror4(x: float, y: float) -> list[Point]:
    """(±x, ±y) — four symmetric points."""
    return [(x, y), (-x, y), (-x, -y), (x, -y)]

# ---- app ------------------------------------------------------------





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

    def hide_construction(self) -> None:
        """Hide construction planes and axes for the whole document (view setting only)."""
        for prop in ("hideAllPlanes", "hideAllAxis"):
            try:
                setattr(self.doc, prop, True)
            except Exception:
                pass

    def save(self, path: str | Path) -> Path:
        self.hide_construction()
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.doc.SaveAs(str(path)):
            raise RuntimeError(f"KOMPAS failed to save {path}")
        return path

# ---- vtulka ------------------------------------------------------------




Pt = tuple[float, float]


@dataclass
class VtulkaParams:
    length: float = 80.5
    # Flange Ø58, 6.5 thick, 1×45° chamfers, 90° notch at the top down to the hub
    flange_d: float = 58.0
    flange_t: float = 6.5
    flange_chamfer: float = 1.0
    notch_angle: float = 90.0
    # Hub behind the flange, then a relief groove, then the Ø37 / Ø35h6 seat
    hub_d: float = 37.0
    hub_end: float = 11.0
    relief_d: float = 33.0  # est.
    band_x: float = 13.5
    band_d: float = 37.0
    band_chamfer: float = 1.0
    seat_x: float = 20.5
    seat_d: float = 35.0
    seat_end: float = 28.5
    # Ø18 neck, then the Ø20f7 shaft
    neck_d: float = 18.0
    shaft_x: float = 35.5
    shaft_d: float = 20.0
    shaft_chamfer: float = 1.0  # 45° step Ø18 → Ø20
    # 4 grooves 2 wide, left edges 40/33/26/19 from the tip, bottom Ø19
    groove_from_end: tuple[float, ...] = (40.0, 33.0, 26.0, 19.0)
    groove_w: float = 2.0
    groove_d: float = 19.0
    # Retaining groove at the tip (detail E): Ø16.7h12, 2 wide, 2 from the end
    ring_groove_d: float = 16.7
    ring_groove_w: float = 2.0
    ring_lip: float = 2.0
    tip_chamfer: float = 0.5
    # Two flats on the tip (Б-Б), 14 across, 6 long, ending 11 from the tip
    flats_across: float = 14.0
    flats_from_end: float = 11.0
    flats_len: float = 6.0
    # Bore and pocket
    bore_d: float = 10.0
    pocket_d: float = 30.0
    pocket_relief_d: float = 31.0
    pocket_relief_x: float = 15.0  # est.
    pocket_depth: float = 18.0  # est.
    pocket_chamfer: float = 1.0
    # 4 × M3 through the flange on Ø50 at ±30°/±150°
    flange_holes_pcd: float = 50.0
    flange_holes_angles: tuple[float, ...] = (30.0, 150.0, 210.0, 330.0)
    # 4 × M3×5 in the pocket floor on Ø28 at 45°
    floor_holes_pcd: float = 28.0
    floor_holes_angles: tuple[float, ...] = (45.0, 135.0, 225.0, 315.0)
    floor_holes_depth: float = 5.0
    tap_d: float = 2.5  # M3 tap drill
    material: str = "АК6 ГОСТ 4784-97"
    density: float = 2.75  # g/cm3
    name: str = "Втулка"

    @property
    def grooves(self) -> list[tuple[float, float]]:
        return sorted((self.length - g, self.groove_w) for g in self.groove_from_end)

    def validate(self) -> None:
        xs = [0, self.flange_t, self.hub_end, self.band_x, self.seat_x, self.seat_end,
              self.shaft_x, self.length - self.flats_from_end,
              self.length - self.ring_lip - self.ring_groove_w, self.length]
        if xs != sorted(xs):
            raise ValueError("step positions must increase along the part")
        if self.relief_d / 2 - self.pocket_d / 2 < 1.0:
            raise ValueError("relief groove breaks into the pocket")
        if self.flats_across / 2 - self.bore_d / 2 < 1.0:
            raise ValueError("tip flats break into the bore")
        if self.ring_groove_d / 2 - self.bore_d / 2 < 1.0:
            raise ValueError("retaining groove breaks into the bore")
        r = self.floor_holes_pcd / 2
        if not (self.bore_d / 2 < r - self.tap_d / 2 and r + self.tap_d / 2 < self.pocket_relief_d / 2):
            raise ValueError("floor holes must sit on the pocket floor")
        if self.pocket_depth + self.floor_holes_depth >= self.seat_end:
            raise ValueError("floor holes run past the seat")


def outer_profile(p: VtulkaParams) -> list[Pt]:
    """(x, radius) from the flange face to the tip."""
    R, fc, bc = p.flange_d / 2, p.flange_chamfer, p.band_chamfer
    pts: list[Pt] = [
        (0, R - fc), (fc, R), (p.flange_t - fc, R), (p.flange_t, R - fc),
        (p.flange_t, p.hub_d / 2), (p.hub_end, p.hub_d / 2),
        (p.hub_end, p.relief_d / 2), (p.band_x, p.relief_d / 2),
        (p.band_x, p.band_d / 2 - bc), (p.band_x + bc, p.band_d / 2),
        (p.seat_x, p.band_d / 2), (p.seat_x, p.seat_d / 2),
        (p.seat_end, p.seat_d / 2), (p.seat_end, p.neck_d / 2),
        (p.shaft_x, p.neck_d / 2), (p.shaft_x + p.shaft_chamfer, p.shaft_d / 2),
    ]
    rs, rg = p.shaft_d / 2, p.groove_d / 2
    for gx, gw in p.grooves:
        pts += [(gx, rs), (gx, rg), (gx + gw, rg), (gx + gw, rs)]
    g0 = p.length - p.ring_lip - p.ring_groove_w
    pts += [(g0, rs), (g0, p.ring_groove_d / 2), (g0 + p.ring_groove_w, p.ring_groove_d / 2),
            (g0 + p.ring_groove_w, rs), (p.length - p.tip_chamfer, rs),
            (p.length, rs - p.tip_chamfer)]
    return pts


def inner_profile(p: VtulkaParams) -> list[Pt]:
    """(x, radius) from the tip back to the flange face."""
    rp, pc = p.pocket_d / 2, p.pocket_chamfer
    return [
        (p.length, p.bore_d / 2), (p.pocket_depth, p.bore_d / 2),
        (p.pocket_depth, p.pocket_relief_d / 2), (p.pocket_relief_x, p.pocket_relief_d / 2),
        (p.pocket_relief_x, rp), (pc, rp), (0, rp + pc),
    ]


def section_profile(p: VtulkaParams) -> list[Pt]:
    return outer_profile(p) + inner_profile(p)


def notch_region(p: VtulkaParams) -> tuple[list[Pt], Pt, Pt]:
    """Notch outline in (Z, Y): polyline from the right inner corner round the outside
    to the left inner corner; closed by an arc on the hub radius through the top."""
    h = math.radians(p.notch_angle / 2)
    ri, ro = p.hub_d / 2, p.flange_d / 2 + 5
    sa, ca = math.sin(math.pi / 2 - h), math.cos(math.pi / 2 - h)
    right_in, left_in = (ri * ca, ri * sa), (-ri * ca, ri * sa)
    pts = [right_in, (ro * ca, ro * sa), (ro * ca, ro), (-ro * ca, ro), (-ro * ca, ro * sa), left_in]
    return pts, left_in, right_in


def on_circle(pcd: float, angles) -> list[Pt]:
    r = pcd / 2
    return [(r * math.cos(math.radians(a)), r * math.sin(math.radians(a))) for a in angles]


def build(pb: PartBuilder, p: VtulkaParams | None = None) -> PartBuilder:
    p = p or VtulkaParams()
    p.validate()
    e = 1.0

    # 1. Turned body
    pb.revolve("XY", section_profile(p), ((-5.0, 0.0), (p.length + 5.0, 0.0)))

    # 2. 90° notch in the flange, down to the hub radius
    def notch(s):
        pts, left_in, right_in = notch_region(p)
        s.polyline(pts, closed=False)
        s.arc((0.0, 0.0), left_in, right_in, ccw=False)
    pb.prism("YZ", -e, p.flange_t, notch, cut=True)

    # 3. 4 × M3 through the flange; 4 × M3×5 in the pocket floor
    pb.prism("YZ", -e, p.flange_t + e,
             lambda s: s.circles(on_circle(p.flange_holes_pcd, p.flange_holes_angles), p.tap_d),
             cut=True)
    pb.prism("YZ", p.pocket_depth - e, p.pocket_depth + p.floor_holes_depth,
             lambda s: s.circles(on_circle(p.floor_holes_pcd, p.floor_holes_angles), p.tap_d),
             cut=True)

    # 4. Two flats on the tip
    x1 = p.length - p.flats_from_end
    x0, big, half = x1 - p.flats_len, p.shaft_d, p.flats_across / 2
    pb.through("XY", lambda s: (s.box(x0, half, x1, big), s.box(x0, -big, x1, -half)))

    pb.update()
    return pb


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs KOMPAS

    ap = argparse.ArgumentParser(description="Build «Втулка» in KOMPAS-3D v24")
    ap.add_argument("--out", default="vtulka.m3d", help="output .m3d path")
    ap.add_argument("--hidden", action="store_true", help="build without showing the document")
    ap.add_argument("--no-calibrate", action="store_true",
                    help="skip measuring sketch axes (uses built-in guess)")
    args = ap.parse_args(argv)

    params = VtulkaParams()
    app = KompasApp(visible=not args.hidden)
    orientation = FALLBACK if args.no_calibrate else app.calibrate()
    doc = app.new_part(params.name, visible=not args.hidden, orientation=orientation)
    build(doc.builder, params)
    try:
        doc.set_material(params.material, params.density)
    except Exception as exc:
        print(f"warning: material not set: {exc}")
    try:
        mass, _ = doc.mass_center()
        print(f"mass: {mass * 1000:.0f} g")
    except Exception:
        pass
    print(f"saved {doc.save(args.out)}")


if __name__ == "__main__":  # pragma: no cover
    main()
