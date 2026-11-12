"""«Корпус» -002-001 (Сталь 45 ГОСТ 1050-2013, 3.62 kg) — model from the A3 drawing.

World axes (KOMPAS defaults, see orientation.AXES):
  X — width, left → right in the front view (0 … 122)
  Y — height, bottom → top (0 … 97)
  Z — depth, back → front; the front view looks at the Z = 80 face (0 … 80)

Dimensions marked "est." are not given on the drawing and were measured off
the scan. Note 2 (0.2 mm edge breaks), coating and tolerances are not modelled.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

from ..part import PartBuilder


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
    from ..app import KompasApp
    from ..orientation import FALLBACK

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
