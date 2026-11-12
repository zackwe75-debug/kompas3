"""Втулка model against the fake KOMPAS."""

import math

import pytest
from test_cylinder import FakeKompas
from test_korpus import TRUE

from kompas_sdk import KompasApp
from kompas_sdk import constants as c
from kompas_sdk.models.vtulka import VtulkaParams, build, notch_region, on_circle, section_profile


@pytest.fixture
def built():
    fake = FakeKompas()
    build(KompasApp(kompas=fake).new_part(orientation=TRUE).builder)
    return fake


def test_features(built):
    kinds = [e.kind for e in built.doc.part.entities]
    assert kinds.count(c.o3d_baseRotated) == 1
    assert kinds.count(c.o3d_cutExtrusion) == 4


def test_profile_closed_and_positive():
    poly = section_profile(VtulkaParams())
    xs = [x for x, _ in poly]
    assert min(xs) == 0 and max(xs) == 80.5
    assert all(r > 0 for _, r in poly)
    assert len(set(poly)) == len(poly)


def test_grooves_match_drawing():
    p = VtulkaParams()
    assert [g for g, _ in p.grooves] == [40.5, 47.5, 54.5, 61.5]


def test_notch_spans_90_degrees():
    pts, left_in, right_in = notch_region(VtulkaParams())
    for z, y in (left_in, right_in):
        assert math.hypot(z, y) == pytest.approx(18.5)
        assert math.degrees(math.atan2(y, abs(z))) == pytest.approx(45.0)
    assert pts[0] == right_in and pts[-1] == left_in


def test_notch_arc_passes_through_top(built):
    arcs = [a for n, m, a in built._log if n == "doc2d" and m == "ksArcByPoint"]
    assert len(arcs) == 1


def test_holes():
    p = VtulkaParams()
    for z, y in on_circle(p.flange_holes_pcd, p.flange_holes_angles):
        assert math.hypot(z, y) == pytest.approx(25.0)
        assert not (45 < math.degrees(math.atan2(y, z)) < 135)  # clear of the notch


def test_mass_reasonable():
    """Revolve volume via Pappus, minus the notch, for АК6."""
    p = VtulkaParams()
    poly = section_profile(p)
    a = cy = 0.0
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        cr = x1 * y2 - x2 * y1
        a += cr
        cy += (y1 + y2) * cr
    vol = abs(2 * math.pi * cy / 6)
    notch = math.pi * (29 ** 2 - 18.5 ** 2) * p.flange_t / 4
    grams = (vol - notch) * p.density / 1000
    assert 60 < grams < 110


def test_rejects_bad_params():
    with pytest.raises(ValueError):
        build(KompasApp(kompas=FakeKompas()).new_part(orientation=TRUE).builder,
              VtulkaParams(flats_across=11.0))
