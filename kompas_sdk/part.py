"""Feature-level builder over the KOMPAS API5 ``ksPart`` object."""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Callable, Iterable, Iterator

from . import constants as c
from .orientation import Orientation

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
