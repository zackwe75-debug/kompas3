"""KOMPAS-3D v24: build the «kartridzh» part (single-file version of kompas_sdk).

Usage (Windows, KOMPAS-3D v24, 64-bit Python):
    pip install pywin32
    python kompas_kartridzh.py --out kartridzh.m3d
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
        try:
            entity.hidden = True  # construction plane; keep the model view clean
        except Exception:
            pass
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

    def save(self, path: str | Path) -> Path:
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.doc.SaveAs(str(path)):
            raise RuntimeError(f"KOMPAS failed to save {path}")
        return path

# ---- kartridzh ------------------------------------------------------------




Pt = tuple[float, float]


@dataclass
class KartridzhParams:
    length: float = 351.0
    # Outer profile (x, diameter) — main view
    tube_d: float = 70.0
    tube_l: float = 75.0
    neck_d: float = 58.0  # Ø58* in В-В
    neck_x0: float = 106.0  # est.
    neck_x1: float = 114.0  # est. (R20 transition replaced by a cone)
    shoulder_x: float = 124.0
    shoulder_d: float = 73.0
    collar_x: float = 149.0  # 124 + 25
    collar_d: float = 78.0
    collar_l: float = 14.5  # est.
    neck2_d: float = 72.0
    body_x: float = 181.0  # 351 - 170
    body_d: float = 78.0
    flange_end_x: float = 307.0  # 351 - 44
    spigot_d: float = 48.93
    # O-ring grooves on the body (bottom Ø73.25), x of left edge and width
    body_grooves: tuple[tuple[float, float], ...] = ((241.0, 4.0), (256.0, 6.0))
    body_groove_d: float = 73.25
    # Spigot grooves
    spigot_grooves: tuple[tuple[float, float], ...] = ((330.0, 5.0), (338.0, 5.0))
    spigot_groove_d: float = 44.0  # est.
    # Inner bores (A-A)
    mouth_d: float = 59.05
    mouth_l: float = 20.0
    bore_d: float = 55.95
    bore_l: float = 61.0
    undercut_d: float = 59.0
    undercut_l: float = 70.0
    pocket_d: float = 36.0  # est.
    pocket_l: float = 112.0  # est.
    pocket_tip_l: float = 115.0
    passage_d: float = 26.0  # Ø26±1
    passage_end: float = 200.0  # est.
    outlet_x: float = 241.0  # 351 - 110
    outlet_d: float = 37.0
    end_bore_d: float = 43.0
    end_bore_l: float = 21.0
    # Side window (Л-Л / Д-Д): straight cut from +Y
    window_x: tuple[float, float] = (108.0, 143.0)  # est.
    window_w: float = 34.51
    window_y0: float = 8.0  # est.
    # Wrench flats (И-И) on x 270..300, sides 25° from vertical
    flats_x: tuple[float, float] = (270.0, 300.0)
    flats_half_w: float = 28.0  # est. half-width at the axis
    flats_angle: float = 25.0
    # 4 × Ø8.5 on Ø64 through the flange (x 300..307), ±25° from horizontal
    flange_holes_d: float = 8.5
    flange_pcd: float = 64.0
    flange_holes_angle: float = 25.0  # est.
    material: str = "Сталь 08Х15Н4ДМЛ"
    density: float = 7.75  # g/cm3
    name: str = "Картридж выпрямителя потока"
    designation: str = "000.10172583"

    def validate(self) -> None:
        xs = [0, self.tube_l, self.neck_x0, self.neck_x1, self.shoulder_x, self.collar_x,
              self.collar_x + self.collar_l, self.body_x, self.flats_x[0], self.flats_x[1],
              self.flange_end_x, self.length]
        if xs != sorted(xs):
            raise ValueError("outer step positions must increase along the part")
        prof = section_profile(self)
        for x, r in inner_profile(self):
            if r >= outer_radius(self, x) - 1.0:
                raise ValueError(f"wall thinner than 1 mm at x={x}")
        if len(prof) < 4:
            raise ValueError("degenerate profile")
        hx = self.flange_pcd / 2 * math.cos(math.radians(self.flange_holes_angle))
        hy = self.flange_pcd / 2 * math.sin(math.radians(self.flange_holes_angle))
        side_x = self.flats_half_w - hy * math.tan(math.radians(self.flats_angle))
        if hx - self.flange_holes_d / 2 <= side_x:
            raise ValueError("flange holes intersect the wrench flats")


def _grooved(x0: float, x1: float, r: float, grooves, r_groove: float) -> list[Pt]:
    pts: list[Pt] = [(x0, r)]
    for gx, gw in grooves:
        if x0 < gx and gx + gw < x1:
            pts += [(gx, r), (gx, r_groove), (gx + gw, r_groove), (gx + gw, r)]
    return pts + [(x1, r)]


def outer_profile(p: KartridzhParams) -> list[Pt]:
    """(x, radius) from the tube end to the spigot end."""
    c_end = p.collar_x + p.collar_l
    pts: list[Pt] = [
        (0, p.tube_d / 2), (p.tube_l, p.tube_d / 2), (p.neck_x0, p.neck_d / 2),
        (p.neck_x1, p.neck_d / 2), (p.shoulder_x, p.shoulder_d / 2), (p.collar_x, p.shoulder_d / 2),
        (p.collar_x, p.collar_d / 2), (c_end, p.collar_d / 2), (c_end, p.neck2_d / 2),
        (p.body_x, p.neck2_d / 2),
    ]
    pts += _grooved(p.body_x, p.flange_end_x, p.body_d / 2, p.body_grooves, p.body_groove_d / 2)
    pts += _grooved(p.flange_end_x, p.length, p.spigot_d / 2, p.spigot_grooves, p.spigot_groove_d / 2)
    # drop consecutive duplicates (step points meeting groove edges)
    return [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]


def inner_profile(p: KartridzhParams) -> list[Pt]:
    """(x, radius) from the spigot end back to the tube end."""
    L = p.length
    return [
        (L, p.end_bore_d / 2), (L - p.end_bore_l, p.end_bore_d / 2),
        (L - p.end_bore_l, p.outlet_d / 2), (p.outlet_x, p.outlet_d / 2),
        (p.passage_end, p.passage_d / 2), (p.pocket_tip_l, p.passage_d / 2),
        (p.pocket_l, p.pocket_d / 2), (p.undercut_l, p.pocket_d / 2),
        (p.undercut_l, p.undercut_d / 2), (p.bore_l, p.undercut_d / 2),
        (p.bore_l, p.bore_d / 2), (p.mouth_l, p.bore_d / 2),
        (p.mouth_l, p.mouth_d / 2), (0, p.mouth_d / 2),
    ]


def section_profile(p: KartridzhParams) -> list[Pt]:
    """Closed half-section (x, r) for the revolve."""
    return outer_profile(p) + inner_profile(p)


def outer_radius(p: KartridzhParams, x: float) -> float:
    pts = outer_profile(p)
    best = 0.0
    for (x0, r0), (x1, r1) in zip(pts, pts[1:]):
        if x0 <= x <= x1 and x1 > x0:
            best = max(best, min(r0 + (r1 - r0) * (x - x0) / (x1 - x0), max(r0, r1)))
    return best


def flat_cut_regions(p: KartridzhParams, big: float = 45.0) -> list[list[Pt]]:
    """Two regions (Z, Y) outside the wrench-flat sides, in YZ world coords."""
    t = math.tan(math.radians(p.flats_angle))
    w = p.flats_half_w
    top = w - big * t
    right = [(top, big), (big, big), (big, -big), (top, -big), (w, 0)]
    return [right, [(-z, y) for z, y in right]]


def flange_holes(p: KartridzhParams) -> list[Pt]:
    r, a = p.flange_pcd / 2, math.radians(p.flange_holes_angle)
    return [(sz * r * math.cos(a), sy * r * math.sin(a)) for sz in (1, -1) for sy in (1, -1)]


def build(pb: PartBuilder, p: KartridzhParams | None = None) -> PartBuilder:
    p = p or KartridzhParams()
    p.validate()
    e = 1.0

    # 1. Revolved body (profile in the upper half of the XY plane, axis = X)
    pb.revolve("XY", section_profile(p), ((-10.0, 0.0), (p.length + 10.0, 0.0)))
    # 2. Side window from +Y
    x0, x1 = p.window_x
    pb.prism("XZ", p.window_y0, p.body_d / 2 + e,
             lambda s: s.box(x0, -p.window_w / 2, x1, p.window_w / 2), cut=True)
    # 3. Wrench flats
    fx0, fx1 = p.flats_x
    pb.prism("YZ", fx0, fx1, lambda s: [s.polyline(r) for r in flat_cut_regions(p)], cut=True)
    # 4. 4 × Ø8.5 axial holes through the flange
    pb.prism("YZ", fx1 - e, p.flange_end_x + e,
             lambda s: s.circles(flange_holes(p), p.flange_holes_d), cut=True)

    pb.update()
    return pb


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs KOMPAS

    ap = argparse.ArgumentParser(description="Build «Картридж выпрямителя потока» in KOMPAS-3D v24")
    ap.add_argument("--out", default="kartridzh.m3d", help="output .m3d path")
    ap.add_argument("--hidden", action="store_true", help="build without showing the document")
    ap.add_argument("--no-calibrate", action="store_true",
                    help="skip measuring sketch axes (uses built-in guess)")
    args = ap.parse_args(argv)

    params = KartridzhParams()
    app = KompasApp(visible=not args.hidden)
    orientation = FALLBACK if args.no_calibrate else app.calibrate()
    doc = app.new_part(params.name, visible=not args.hidden, orientation=orientation)
    try:
        doc.part.marking = params.designation
    except Exception:
        pass
    build(doc.builder, params)
    try:
        doc.set_material(params.material, params.density)
    except Exception as exc:
        print(f"warning: material not set: {exc}")
    try:
        mass, _ = doc.mass_center()
        print(f"mass: {mass:.2f} kg (drawing: 6.45 kg)")
    except Exception:
        pass
    print(f"saved {doc.save(args.out)}")


if __name__ == "__main__":  # pragma: no cover
    main()
