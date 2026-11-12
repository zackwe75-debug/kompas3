"""KOMPAS-3D v24: build the «korpus» part (single-file version of kompas_sdk).

Usage (Windows, KOMPAS-3D v24, 64-bit Python):
    pip install pywin32
    python kompas_korpus.py --out korpus.m3d
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

    def line(self, p1: Point, p2: Point):
        (x1, y1), (x2, y2) = self._m(*p1), self._m(*p2)
        return self.doc2d.ksLineSeg(x1, y1, x2, y2, c.STYLE_MAIN)

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

# ---- korpus ------------------------------------------------------------





@dataclass
class KorpusParams:
    # Main block (X 0..block_w) with the left-view step profile
    width: float = 122.0
    block_w: float = 78.0  # 122 - 44
    height: float = 97.0
    depth: float = 80.0
    top_depth: float = 65.0
    top_h: float = 25.0
    ledge_chamfer: float = 7.0  # 7×45°
    # Front groove 35F8 × 4H10, bottom at 21.5H8 (X 0..recess_x)
    groove_y: float = 21.5
    groove_h: float = 35.0
    groove_depth: float = 4.0
    # Front face X recess_x..block_w set back by groove_depth up to the ledge
    recess_x: float = 25.0
    # Right plate 44 wide, 21 thick, at Z 30..51, 5×45° chamfers (2 pcs)
    plate_z: float = 30.0
    plate_t: float = 21.0
    plate_chamfer: float = 5.0
    # Slot 30E9 from the bottom, through in Z
    slot_x: float = 25.0
    slot_w: float = 30.0
    slot_h: float = 50.0  # est.
    # Vertical bore at X40, Z26 (from back)
    bore_x: float = 40.0
    bore_z: float = 26.0
    bore_d: float = 28.0  # est.
    cbore_d: float = 40.0  # est.
    cbore_depth: float = 10.0  # est.
    # 4 holes on R32.5 at ±30° / ±150° (gives 56.29* and 32.5*)
    bolt_r: float = 32.5
    bolt_angles: tuple[float, ...] = (30.0, 150.0, 210.0, 330.0)
    bolt_d: float = 8.0  # est.
    # Two holes in the upper front face (Z = 65)
    top_holes_x: tuple[float, ...] = (28.0, 68.0)  # est.
    top_holes_y: float = 84.5  # est.
    top_holes_d: float = 5.0  # est.
    top_holes_depth: float = 20.0  # est.
    # Plate holes (front view), counterbored from Z = plate front
    plate_cb_holes: tuple[tuple[float, float], ...] = ((112.0, 84.5), (95.0, 48.5), (112.0, 12.5))
    plate_cb_d: float = 8.5  # est.
    plate_cb_d2: float = 14.0  # est.
    plate_cb_depth: float = 8.5  # est.
    plate_holes: tuple[tuple[float, float], ...] = ((95.0, 76.75), (95.0, 20.25))
    plate_hole_d: float = 8.0  # est.
    # Two holes and a relief slot in the groove face (left part)
    left_holes_x: float = 10.0  # est.
    left_holes_y: tuple[float, ...] = (27.0, 51.0)  # est.
    left_holes_d: float = 6.0  # est.
    left_holes_depth: float = 15.0  # est.
    relief_x: float = 22.0  # est. (X 19..25)
    relief_w: float = 6.0  # est.
    relief_len: float = 32.0  # est. (Y 23..55)
    relief_depth: float = 10.0  # est.
    # Side hole along X (left view circle)
    side_hole_z: float = 60.0  # est.
    side_hole_y: float = 23.6  # est.
    side_hole_d: float = 16.0  # est.
    # 5×45° chamfer, bottom-left edge in the front view
    base_chamfer: float = 5.0
    material: str = "Сталь 45 ГОСТ 1050-2013"
    density: float = 7.82  # g/cm3
    name: str = "Корпус"
    designation: str = "-002-001"

    @property
    def ledge_y(self) -> float:
        return self.height - self.top_h

    def validate(self) -> None:
        if self.block_w >= self.width:
            raise ValueError("block must be narrower than the part")
        if self.groove_y + self.groove_h >= self.ledge_y - self.ledge_chamfer:
            raise ValueError("front groove runs into the ledge chamfer")
        if not self.slot_x + self.slot_w <= self.block_w:
            raise ValueError("30E9 slot outside the block")
        if self.plate_z + self.plate_t > self.depth:
            raise ValueError("plate outside the depth")
        if self.cbore_d <= self.bore_d:
            raise ValueError("counterbore must be larger than the bore")


def side_profile(p: KorpusParams) -> list[tuple[float, float]]:
    """Left-view outline in (Z, Y): step, ledge chamfer and front groove."""
    d, ly, ch = p.depth, p.ledge_y, p.ledge_chamfer
    g0, g1, gd = p.groove_y, p.groove_y + p.groove_h, p.groove_depth
    return [
        (0, 0), (d, 0), (d, g0), (d - gd, g0), (d - gd, g1), (d, g1),
        (d, ly - ch), (d - ch, ly), (p.top_depth, ly), (p.top_depth, p.height), (0, p.height),
    ]


def plate_profile(p: KorpusParams, overlap: float = 8.0) -> list[tuple[float, float]]:
    """Front-view outline (X, Y); starts inside the block so the bodies fuse."""
    x0, x1, h, c = p.block_w - overlap, p.width, p.height, p.plate_chamfer
    return [(x0, 0), (x1 - c, 0), (x1, c), (x1, h - c), (x1 - c, h), (x0, h)]


def bolt_centres(p: KorpusParams) -> list[tuple[float, float]]:
    """(X, Z) of the four holes around the bore."""
    return [(p.bore_x + p.bolt_r * math.cos(math.radians(a)),
             p.bore_z + p.bolt_r * math.sin(math.radians(a))) for a in p.bolt_angles]


def build(pb: PartBuilder, p: KorpusParams | None = None) -> PartBuilder:
    p = p or KorpusParams()
    p.validate()
    d, h, e = p.depth, p.height, 1.0  # e: overshoot so cuts open cleanly
    plate_front = p.plate_z + p.plate_t

    # 1. Block: left-view profile extruded across X 0..78
    pb.prism("YZ", 0, p.block_w, lambda s: s.polyline(side_profile(p)))
    # 2. Right plate with chamfered corners
    pb.prism("XY", p.plate_z, plate_front, lambda s: s.polyline(plate_profile(p)))
    # 3. Front face of X 25..78 set back 4 mm below the ledge
    pb.prism("XY", d - p.groove_depth, d + e,
             lambda s: s.box(p.recess_x, -e, p.block_w, p.ledge_y), cut=True)
    # 4. Slot 30E9 from the bottom, through in Z
    pb.through("XY", lambda s: s.box(p.slot_x, -e, p.slot_x + p.slot_w, p.slot_h))
    # 5. Vertical bore with counterbore from the top, and 4 holes on R32.5
    pb.through("XZ", lambda s: s.circle((p.bore_x, p.bore_z), p.bore_d))
    pb.prism("XZ", h - p.cbore_depth, h + e,
             lambda s: s.circle((p.bore_x, p.bore_z), p.cbore_d), cut=True)
    pb.through("XZ", lambda s: s.circles(bolt_centres(p), p.bolt_d))
    # 6. Holes in the upper front face
    pb.prism("XY", p.top_depth - p.top_holes_depth, p.top_depth + e,
             lambda s: s.circles([(x, p.top_holes_y) for x in p.top_holes_x], p.top_holes_d),
             cut=True)
    # 7. Plate holes: through, then counterbores from the plate front
    pb.through("XY", lambda s: s.circles(p.plate_cb_holes, p.plate_cb_d))
    pb.through("XY", lambda s: s.circles(p.plate_holes, p.plate_hole_d))
    pb.prism("XY", plate_front - p.plate_cb_depth, plate_front + e,
             lambda s: s.circles(p.plate_cb_holes, p.plate_cb_d2), cut=True)
    # 8. Groove face (Z = 76): two holes and the relief slot
    face = d - p.groove_depth
    pb.prism("XY", face - p.left_holes_depth, face + e,
             lambda s: s.circles([(p.left_holes_x, y) for y in p.left_holes_y], p.left_holes_d),
             cut=True)
    relief_y = p.groove_y + p.groove_h / 2
    pb.prism("XY", face - p.relief_depth, face + e,
             lambda s: s.obround((p.relief_x, relief_y), p.relief_len, p.relief_w, vertical=True),
             cut=True)
    # 9. Side hole along X
    pb.through("YZ", lambda s: s.circle((p.side_hole_z, p.side_hole_y), p.side_hole_d))
    # 10. 5×45° chamfer on the bottom-left edge
    c = p.base_chamfer
    pb.through("XY", lambda s: s.polyline([(-e, -e), (c + e, -e), (-e, c + e)]))

    pb.update()
    return pb


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs KOMPAS

    ap = argparse.ArgumentParser(description="Build «Корпус» -002-001 in KOMPAS-3D v24")
    ap.add_argument("--out", default="korpus.m3d", help="output .m3d path")
    ap.add_argument("--hidden", action="store_true", help="build without showing the document")
    ap.add_argument("--no-calibrate", action="store_true",
                    help="skip measuring sketch axes (uses built-in guess; may mirror features)")
    args = ap.parse_args(argv)

    params = KorpusParams()
    app = KompasApp(visible=not args.hidden)
    if args.no_calibrate:
        orientation = FALLBACK
    else:
        orientation = app.calibrate()
        print("sketch axes:", {k: (v.u, v.v, v.offset_sign) for k, v in orientation.items()})
    doc = app.new_part(params.name, visible=not args.hidden, orientation=orientation)
    try:
        doc.part.marking = params.designation
    except Exception:
        pass
    build(doc.builder, params)
    try:
        doc.set_material(params.material, params.density)
    except Exception as exc:  # material API differs between installs; model is still valid
        print(f"warning: material not set: {exc}")
    try:
        mass, _ = doc.mass_center()
        print(f"mass: {mass:.2f} kg (drawing: 3.62 kg)")
    except Exception:
        pass
    print(f"saved {doc.save(args.out)}")


if __name__ == "__main__":  # pragma: no cover
    main()
