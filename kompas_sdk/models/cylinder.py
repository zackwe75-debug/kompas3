"""«Цилиндр» (cast housing, АЛ8 ГОСТ 2685-75) — parametric model from the A1 drawing.

Orientation: main bore axis = Z, origin at the part centre.
  * end faces (16×M20 flange, 2×M62 on R50)  -> ±Z
  * Ø320 boss with Ø260 bore, 4 lugs M36     -> ±Y
  * 140×140 pad with Ø168 port, 4×M16        -> ±X
  * Ø234 windows, 4 per face                 -> ±Y and ±X (configurable)

Features that the drawing shows on one face only are built mirrored on the
opposite face as well; this keeps every feature independent of KOMPAS plane
orientation. R2 casting fillets are applied to the raw casting. Internal
casting cavities and draft are not modelled. Threads are tap-drill holes.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field

from ..part import BOTH, SYMMETRIC, PartBuilder, mirror4, symmetric_circle


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
    from ..app import KompasApp

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
