# kompas-sdk

Python SDK for building 3D parts in **KOMPAS-3D v24** through its COM API (API5),
plus a parametric model of the «Цилиндр» housing (АЛ8 ГОСТ 2685-75) from the A1 drawing.

## Requirements

- Windows with KOMPAS-3D v24 installed (registered COM server `Kompas.Application.5`)
- Python 3.9+ (match the bitness of KOMPAS, normally 64-bit)
- `pywin32`

```bat
pip install -e .
```

## Single-file script

`kompas_cylinder.py` in the repo root is the whole SDK plus the cylinder model in one file
(no install needed besides `pywin32`):

```bat
python kompas_cylinder.py --out C:\models\cylinder.m3d
```

## Build the cylinder

```bat
kompas-cylinder --out C:\models\cylinder.m3d
:: or
python -m kompas_sdk.models.cylinder --out cylinder.m3d --windows Y
```

KOMPAS starts (or attaches to the running instance), builds the part and saves the `.m3d`.

## Use the SDK

```python
from kompas_sdk import KompasApp, SYMMETRIC, BOTH, bolt_circle

app = KompasApp()
doc = app.new_part("Фланец")
pb = doc.builder

with pb.sketch("XY") as s:
    s.circle((0, 0), 200)
pb.extrude(s, 20, SYMMETRIC)

with pb.sketch("XY") as s:
    s.circles(bolt_circle(160, 8), 12)
pb.cut(s, mode=BOTH, through=True)

doc.save("flange.m3d")
```

`PartBuilder` provides `plane`, `offset_plane`, `offset_plane_pair`, `sketch` (line, arc,
circle, rect, polyline), `extrude` and `cut` with modes `NORMAL`, `REVERSE`, `BOTH`, `SYMMETRIC`,
and `fillet_all_edges`.

## Cylinder model notes

All dimensions live in `CylinderParams` (`kompas_sdk/models/cylinder.py`). Bore axis is Z.
Simplifications versus the drawing:

- internal casting cavities and draft angles are not modelled;
- threads are tap-drill holes (M20 → Ø17.5, M16 → Ø14, M36 → Ø32, M62 → Ø60);
- features shown on one face (Ø320 boss, lugs, 140×140 pad) are mirrored to the opposite face;
- R2 casting fillets (note 1) are applied to every edge of the raw casting before machining;
  if KOMPAS cannot build them it prints a warning and continues (`--no-fillets` skips them).

## Tests

Tests run without KOMPAS using a fake COM object:

```bash
pip install pytest && pytest
```
