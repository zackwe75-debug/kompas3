"""KOMPAS-3D API5 constants (ksConstants3D / ksConstants).

Hardcoded so the SDK works with plain late-bound ``Dispatch`` and does not
depend on generated type-library wrappers. Values match KOMPAS-3D v24.
"""

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
