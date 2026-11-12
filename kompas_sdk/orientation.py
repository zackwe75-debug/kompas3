"""World-coordinate mapping for sketches on the default planes.

KOMPAS orients sketch axes on the XZ/YZ planes in ways that are easy to get
wrong (e.g. the XZ sketch's v axis points along -Z). Instead of trusting a
table, the orientation is measured in KOMPAS itself (see KompasApp.calibrate)
and stored here.

Each plane has fixed *world* 2D axes used by models:
    XY -> (X, Y), normal Z      (front view)
    XZ -> (X, Z), normal Y      (top view)
    YZ -> (Z, Y), normal X      (left view)
"""

from __future__ import annotations

from dataclasses import dataclass

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
