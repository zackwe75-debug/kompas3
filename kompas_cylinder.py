"""KOMPAS-3D v24: build the «cylinder» part (single-file version of kompas_sdk).

Usage (Windows, KOMPAS-3D v24, 64-bit Python):
    pip install pywin32
    python kompas_cylinder.py --out cylinder.m3d
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

# ---- cylinder ------------------------------------------------------------





@dataclass
class CylinderParams:
    # Body: Ø820 cylinder cut by four flats 776 across (end view / view from above)
    body_d: float = 820.0
    flats: float = 776.0
    length: float = 850.0
    # End flanges: Ø595, total length 871 -> 10.5 per end
    flange_d: float = 595.0
    flange_h: float = 10.5
    # Main bore (section A-A)
    bore_d: float = 370.0  # Ø370H9
    bore_front_d: float = 410.0  # Ø410H11
    bore_back_d: float = 375.0  # Ø375H12
    bore_step_depth: float = 160.0
    # 16 × M20-7H on Ø503, depth 40. Per quadrant at 10°/30°/60°/80° from the
    # horizontal axis (drawing: 10° and 30° from the vertical, mirrored).
    flange_pcd: float = 503.0
    flange_hole_angles: tuple[float, ...] = (10.0, 30.0, 60.0, 80.0)
    flange_hole_d: float = 17.5
    flange_hole_depth: float = 40.0
    # 2 × M62 on the end faces, each on an R50 boss raised to the flange face
    end_hole_x: float = 315.0
    end_hole_d: float = 60.0
    end_hole_depth: float = 60.0
    end_boss_r: float = 50.0
    # Unspecified casting fillets (note 1). 0 disables.
    fillet_r: float = 2.0
    # Windows Ø234, 4 per face
    window_d: float = 234.0
    window_offset: float = 250.0
    window_faces: tuple[str, ...] = ("Y", "X")
    # Top boss Ø320 with Ø260 bore
    boss_d: float = 320.0
    boss_h: float = 10.0
    boss_hole_d: float = 260.0
    # Lugs with M36-7H (section Б-Б, 4 places), top at 850/2
    lug_size: float = 60.0
    lug_x: float = 79.5  # 159 / 2
    lug_z: float = 355.0
    lug_top: float = 425.0
    lug_hole_d: float = 32.0
    lug_hole_depth: float = 73.0
    # Side pad 140×140 bolt square, Ø168 port, 4 × M16-7H
    pad_size: float = 200.0
    pad_h: float = 10.0
    pad_hole_d: float = 168.0
    pad_bolt_square: float = 140.0
    pad_bolt_d: float = 14.0
    pad_bolt_depth: float = 30.0
    # Material
    material: str = "АЛ8 ГОСТ 2685-75"
    density: float = 2.55  # g/cm3
    name: str = "Цилиндр"
    extra: dict = field(default_factory=dict)

    def validate(self) -> None:
        r, a = self.body_d / 2, self.flats / 2
        if not a < r:
            raise ValueError("flats must be smaller than body diameter")
        if self.window_offset + self.window_d / 2 >= a:
            raise ValueError("windows do not fit on the body")
        if not self.bore_d < self.bore_back_d <= self.bore_front_d < self.flange_d:
            raise ValueError("bore diameters must satisfy bore < back <= front < flange")
        if self.flange_pcd / 2 + self.flange_hole_d / 2 >= self.flange_d / 2:
            raise ValueError("flange holes outside the flange")
        if self.flange_pcd / 2 - self.flange_hole_d / 2 <= self.bore_front_d / 2:
            raise ValueError("flange holes break into the bore")
        if not all(0 < ang < 90 for ang in self.flange_hole_angles):
            raise ValueError("flange hole angles must be within (0, 90)")
        if self.end_hole_x + self.end_boss_r >= a:
            raise ValueError("M62 bosses outside the body")
        if self.end_boss_r <= self.end_hole_d / 2:
            raise ValueError("M62 boss smaller than its hole")


def body_profile(p: CylinderParams) -> list[tuple[str, tuple]]:
    """Closed outline: circle R cut by four flats at ±a. Returns line/arc segments."""
    r, a = p.body_d / 2, p.flats / 2
    b = math.sqrt(r * r - a * a)
    corners = [((a, -b), (a, b)), ((b, a), (-b, a)), ((-a, b), (-a, -b)), ((-b, -a), (b, -a))]
    segs = []
    for i, (s, e) in enumerate(corners):
        segs.append(("line", (s, e)))
        segs.append(("arc", (e, corners[(i + 1) % 4][0])))
    return segs


def build(pb: PartBuilder, p: CylinderParams | None = None) -> PartBuilder:
    p = p or CylinderParams()
    p.validate()
    half_l, a = p.length / 2, p.flats / 2
    end_face = half_l + p.flange_h

    # 1. Body
    with pb.sketch("XY") as s:
        for kind, (p1, p2) in body_profile(p):
            s.line(p1, p2) if kind == "line" else s.arc((0, 0), p1, p2, ccw=True)
    pb.extrude(s, p.length, SYMMETRIC)

    # 2. End flanges
    end_planes = pb.offset_plane_pair("XY", half_l)
    for plane in end_planes:
        with pb.sketch(plane) as s:
            s.circle((0, 0), p.flange_d)
        pb.extrude(s, 2 * p.flange_h, SYMMETRIC)
        # Separate sketch: the R50 circles overlap the flange circle
        with pb.sketch(plane) as s:
            s.circles([(p.end_hole_x, 0), (-p.end_hole_x, 0)], 2 * p.end_boss_r)
        pb.extrude(s, 2 * p.flange_h, SYMMETRIC)

    # 3. Bosses on ±Y (built from the centre plane so they always merge with the body)
    with pb.sketch("XZ") as s:
        s.circle((0, 0), p.boss_d)
    pb.extrude(s, a + p.boss_h, BOTH)

    # 4. Lugs on ±Y
    with pb.sketch("XZ") as s:
        s.rects(mirror4(p.lug_x, p.lug_z), p.lug_size, p.lug_size)
    pb.extrude(s, p.lug_top, BOTH)

    # 5. Pads on ±X
    with pb.sketch("YZ") as s:
        s.rect((0, 0), p.pad_size, p.pad_size)
    pb.extrude(s, a + p.pad_h, BOTH)

    # Casting fillets go on the raw casting, before any machined cuts
    if p.fillet_r > 0 and not pb.fillet_all_edges(p.fillet_r):
        print(f"warning: R{p.fillet_r} casting fillets could not be built; continuing without them")

    # 6. Main bore with stepped ends
    with pb.sketch("XY") as s:
        s.circle((0, 0), p.bore_d)
    pb.cut(s, mode=BOTH, through=True)
    face_planes = pb.offset_plane_pair("XY", end_face)
    for plane, d in zip(face_planes, (p.bore_front_d, p.bore_back_d)):
        with pb.sketch(plane) as s:
            s.circle((0, 0), d)
        pb.cut(s, 2 * p.bore_step_depth, SYMMETRIC)

    # 7. Flange holes and M62 holes on both ends
    for plane in face_planes:
        with pb.sketch(plane) as s:
            s.circles(symmetric_circle(p.flange_pcd, p.flange_hole_angles), p.flange_hole_d)
        pb.cut(s, 2 * p.flange_hole_depth, SYMMETRIC)
        with pb.sketch(plane) as s:
            s.circles([(p.end_hole_x, 0), (-p.end_hole_x, 0)], p.end_hole_d)
        pb.cut(s, 2 * p.end_hole_depth, SYMMETRIC)

    # 8. Windows
    side_plane = {"Y": "XZ", "X": "YZ"}
    for face in p.window_faces:
        with pb.sketch(side_plane[face]) as s:
            s.circles(mirror4(p.window_offset, p.window_offset), p.window_d)
        pb.cut(s, mode=BOTH, through=True)

    # 9. Boss bore Ø260 (±Y) and pad port Ø168 (±X)
    with pb.sketch("XZ") as s:
        s.circle((0, 0), p.boss_hole_d)
    pb.cut(s, mode=BOTH, through=True)
    with pb.sketch("YZ") as s:
        s.circle((0, 0), p.pad_hole_d)
    pb.cut(s, mode=BOTH, through=True)

    # 10. M16 on pads, M36 in lugs
    for plane in pb.offset_plane_pair("YZ", a + p.pad_h):
        with pb.sketch(plane) as s:
            s.circles(mirror4(p.pad_bolt_square / 2, p.pad_bolt_square / 2), p.pad_bolt_d)
        pb.cut(s, 2 * p.pad_bolt_depth, SYMMETRIC)
    for plane in pb.offset_plane_pair("XZ", p.lug_top):
        with pb.sketch(plane) as s:
            s.circles(mirror4(p.lug_x, p.lug_z), p.lug_hole_d)
        pb.cut(s, 2 * p.lug_hole_depth, SYMMETRIC)

    pb.update()
    return pb


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs KOMPAS

    ap = argparse.ArgumentParser(description="Build the «Цилиндр» part in KOMPAS-3D v24")
    ap.add_argument("--out", default="cylinder.m3d", help="output .m3d path")
    ap.add_argument("--hidden", action="store_true", help="build without showing the document")
    ap.add_argument("--windows", default="YX", help="faces with Ø234 windows: Y, X, YX")
    ap.add_argument("--no-fillets", action="store_true", help="skip the R2 casting fillets")
    args = ap.parse_args(argv)

    params = CylinderParams(window_faces=tuple(args.windows.upper()))
    if args.no_fillets:
        params.fillet_r = 0.0
    app = KompasApp(visible=not args.hidden)
    doc = app.new_part(params.name, visible=not args.hidden)
    build(doc.builder, params)
    try:
        doc.set_material(params.material, params.density)
    except Exception as exc:  # material API differs between installs; model is still valid
        print(f"warning: material not set: {exc}")
    print(f"saved {doc.save(args.out)}")


if __name__ == "__main__":  # pragma: no cover
    main()
