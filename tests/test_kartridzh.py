"""Flow-straightener cartridge model against the fake KOMPAS."""

import math

import pytest
from test_cylinder import FakeKompas
from test_korpus import TRUE

from kompas_sdk import KompasApp
from kompas_sdk import constants as c
from kompas_sdk.models.kartridzh import (
    KartridzhParams,
    build,
    flange_holes,
    section_profile,
)


@pytest.fixture
def built():
    fake = FakeKompas()
    build(KompasApp(kompas=fake).new_part(orientation=TRUE).builder)
    return fake


def test_features(built):
    kinds = [e.kind for e in built.doc.part.entities]
    assert kinds.count(c.o3d_baseRotated) == 1
    assert kinds.count(c.o3d_cutExtrusion) == 3
    rot = next(e for e in built.doc.part.entities if e.kind == c.o3d_baseRotated)
    assert ("def27", "SetSideParam", (True, 360.0)) in built._log
    assert rot.definition.props["directionType"] == c.dtNormal


def test_axis_line_drawn_with_axis_style(built):
    lines = [a for n, m, a in built._log if n == "doc2d" and m == "ksLineSeg"]
    axis = [a for a in lines if a[4] == c.STYLE_AXIS]
    assert axis == [(-10.0, 0.0, 361.0, 0.0, c.STYLE_AXIS)]  # XY maps 1:1 in TRUE


def test_profile_closed_and_mass_close_to_drawing():
    p = KartridzhParams()
    poly = section_profile(p)
    xs = [x for x, _ in poly]
    assert min(xs) == 0 and max(xs) == 351
    assert all(r > 0 for _, r in poly)
    a = cy = 0.0
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        cr = x1 * y2 - x2 * y1
        a += cr
        cy += (y1 + y2) * cr
    vol = abs(2 * math.pi * cy / 6)  # Pappus
    mass = vol * p.density * 1e-6
    assert 6.4 < mass < 7.4  # revolve only; cuts take ~0.6 kg off; drawing 6.45 kg


def test_flange_holes_on_pcd():
    for z, y in flange_holes(KartridzhParams()):
        assert math.hypot(z, y) == pytest.approx(32.0)


def test_rejects_thin_wall():
    with pytest.raises(ValueError):
        build(KompasApp(kompas=FakeKompas()).new_part(orientation=TRUE).builder,
              KartridzhParams(outlet_d=77.0))
