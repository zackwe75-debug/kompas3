"""«Втулка» (АК6 ГОСТ 4784-97) — model from the A2 drawing.

World axes: part axis = X, x = 0 at the flange face (pocket side, arrow B),
x = 80.5 at the shaft tip. Y is up in section A-A (the flange notch opens to +Y).

Not modelled: the two half-moon openings in the pocket floor, the 3 × M3 holes of
view B, R1/R3 fillets, coating. Threads are tap-drill holes. Dimensions marked
"est." are not given on the drawing and were read off the scan.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from ..part import PartBuilder

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
    from ..app import KompasApp
    from ..orientation import FALLBACK

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
