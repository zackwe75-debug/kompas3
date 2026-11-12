"""Corpus model + orientation calibration, against the fake KOMPAS from test_cylinder."""

from types import SimpleNamespace

import pytest
from test_cylinder import FakeDoc, FakeKompas, FakePart

from kompas_sdk import KompasApp
from kompas_sdk import constants as c
from kompas_sdk.models.korpus import KorpusParams, bolt_centres, build, side_profile
from kompas_sdk.orientation import (
    AXES,
    CAL_OFFSET,
    CAL_T,
    CAL_U,
    CAL_V,
    FALLBACK,
    PlaneOrientation,
    orientation_from_centroids,
)

# A deliberately awkward "true" KOMPAS orientation to prove nothing is hardcoded
TRUE = {
    "XY": PlaneOrientation(("X", 1), ("Y", 1), 1),
    "XZ": PlaneOrientation(("X", 1), ("Z", -1), -1),
    "YZ": PlaneOrientation(("Y", -1), ("Z", 1), 1),
}
IDX = {"X": 0, "Y": 1, "Z": 2}


def vec(axis_sign, scale):
    v = [0.0, 0.0, 0.0]
    v[IDX[axis_sign[0]]] = axis_sign[1] * scale
    return v


def probe_centroids(plane, o):
    c1 = [a + b for a, b in zip(vec(o.u, CAL_U), vec(o.v, CAL_V))]
    n = AXES[plane][2]
    added = [a + b for a, b in zip(c1, vec((n, o.offset_sign), CAL_OFFSET + CAL_T / 4))]
    m1, ma = 1.0, 9.5
    c2 = [(m1 * a + ma * b) / (m1 + ma) for a, b in zip(c1, added)]
    return c1, m1, c2, m1 + ma


@pytest.mark.parametrize("plane", list(AXES))
def test_orientation_from_centroids(plane):
    assert orientation_from_centroids(plane, *probe_centroids(plane, TRUE[plane])) == TRUE[plane]


def test_orientation_rejects_garbage():
    with pytest.raises(RuntimeError):
        orientation_from_centroids("XY", (1, 2, 3), 1, (1, 2, 3), 2)


class CalPart(FakePart):
    """Answers mass queries as a KOMPAS with orientation TRUE would."""

    def __init__(self, log, plane_holder):
        super().__init__(log)
        self.__dict__["holder"] = plane_holder

    def GetDefaultEntity(self, kind):
        self.holder["plane"] = {c.o3d_planeXOY: "XY", c.o3d_planeXOZ: "XZ", c.o3d_planeYOZ: "YZ"}[kind]
        return ("default", kind)

    def CalcMassInertiaProperties(self, bits):
        assert bits == c.ST_MIX_MM | c.ST_MIX_KG
        plane = self.holder["plane"]
        c1, m1, c2, m2 = probe_centroids(plane, TRUE[plane])
        n_ext = sum(e.kind in (c.o3d_baseExtrusion, c.o3d_bossExtrusion) for e in self.entities)
        m, cc = (m1, c1) if n_ext == 1 else (m2, c2)
        return SimpleNamespace(m=m, xc=cc[0], yc=cc[1], zc=cc[2])


def test_calibrate_end_to_end():
    fake, holder = FakeKompas(), {}

    def new_doc():
        doc = FakeDoc(fake._log)
        doc.__dict__["part"] = CalPart(fake._log, holder)
        return doc

    fake.__dict__["Document3D"] = new_doc
    assert KompasApp(kompas=fake).calibrate() == TRUE
    assert sum(1 for n, m, _ in fake._log if n == "doc" and m == "close") == 3


@pytest.fixture
def korpus():
    fake = FakeKompas()
    doc = KompasApp(kompas=fake).new_part("Корпус", orientation=TRUE)
    build(doc.builder)
    return fake




def test_feature_counts(korpus):
    kinds = [e.kind for e in korpus.doc.part.entities]
    assert kinds.count(c.o3d_baseExtrusion) == 1
    assert kinds.count(c.o3d_bossExtrusion) == 1  # plate
    assert kinds.count(c.o3d_cutExtrusion) == 13


def test_world_coordinates_are_mapped(korpus):
    circles = [a for n, m, a in korpus._log if n == "doc2d" and m == "ksCircle"]
    # Side hole is on YZ: world (Z=60, Y=23.6) -> TRUE YZ local (u=-Y, v=Z)
    assert (-23.6, 60.0, 8.0, c.STYLE_MAIN) in circles
    # Bore on XZ: world (X=40, Z=26) -> local (X, -Z)
    assert (40.0, -26.0, 14.0, c.STYLE_MAIN) in circles
    # Bolt holes: 56.29* and 32.5* reference dims
    (x1, z1), (x2, _), _, (_, z4) = bolt_centres(KorpusParams())
    assert x1 - x2 == pytest.approx(56.29, abs=0.01) and z1 - z4 == pytest.approx(32.5)


def test_offset_planes_respect_orientation(korpus):
    planes = [e.definition.props for e in korpus.doc.part.entities if e.kind == c.o3d_planeOffset]
    # XZ offset_sign is -1, so the counterbore plane above the part (Y > 0) uses direction False
    cbore = [pp for pp in planes if pp["offset"] == pytest.approx(97 - 10 / 2 + 0.5)]
    assert cbore and cbore[0]["direction"] is False
    # XY offset_sign is +1: plate mid-plane at Z = 40.5 uses direction True
    plate = [pp for pp in planes if pp["offset"] == pytest.approx(40.5)]
    assert plate and plate[0]["direction"] is True


def test_side_profile_closed_and_sized():
    pts = side_profile(KorpusParams())
    zs, ys = zip(*pts)
    assert (min(zs), max(zs), min(ys), max(ys)) == (0, 80, 0, 97)
    area = 0.5 * abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])))
    assert area == pytest.approx(80 * 97 - 15 * 25 - 7 * 7 / 2 - 4 * 35)


def test_arc_direction_flips_on_mirrored_plane():
    assert TRUE["YZ"].flips_handedness("YZ") is False  # (Z,Y)->(-Y,Z): rotation
    assert FALLBACK["XZ"].flips_handedness("XZ") is True  # (X,Z)->(X,-Z): mirror


def test_world_sketch_requires_orientation():
    doc = KompasApp(kompas=FakeKompas()).new_part()
    with pytest.raises(RuntimeError):
        build(doc.builder)
