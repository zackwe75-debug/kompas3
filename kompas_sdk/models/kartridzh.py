"""«Картридж выпрямителя потока» 000.10172583 (Сталь 08Х15Н4ДМЛ, 6.45 kg) — from the A1 drawing.

World axes: part axis = X, x = 0 at the Ø70 tube end, x = 351 at the Ø48.93 spigot end.
Y is up in the main view (the side window opens towards +Y), Z towards the viewer.

Simplified: the body is one revolved section with the outer steps, O-ring grooves and
coaxial inner bores. The real part has an S-shaped eccentric inner passage, a curved
vane window (sections Г-Г…Е-Е), an oblique Ø11 channel (view A) and two pin holes
(Л-Л); here the passage is a coaxial Ø26 bore and the window a straight cut-out.
Dimensions marked "est." are measured off the scan.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from ..part import PartBuilder

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
    from ..app import KompasApp
    from ..orientation import FALLBACK

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
