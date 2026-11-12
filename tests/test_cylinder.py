"""Offline tests: a fake KOMPAS COM object records the API5 calls."""

import math

import pytest

from kompas_sdk import KompasApp
from kompas_sdk import constants as c
from kompas_sdk.models.cylinder import CylinderParams, body_profile, build


class Rec:
    """Accepts any attribute set / method call and records it."""

    def __init__(self, log, name):
        self.__dict__["_log"], self.__dict__["_name"], self.__dict__["props"] = log, name, {}

    def __setattr__(self, k, v):
        self.props[k] = v

    def __getattr__(self, k):
        def call(*args):
            self._log.append((self._name, k, args))
            return True
        return call


class FakeDef(Rec):
    def __init__(self, log, kind):
        super().__init__(log, f"def{kind}")
        self.__dict__["doc2d"] = Rec(log, "doc2d")

    def BeginEdit(self):
        return self.doc2d


class FakeEntity:
    def __init__(self, log, kind):
        self.kind, self.definition = kind, FakeDef(log, kind)

    def GetDefinition(self):
        return self.definition

    def Create(self):
        return True


class FakePart(Rec):
    def __init__(self, log):
        super().__init__(log, "part")
        self.__dict__["entities"] = []

    def NewEntity(self, kind):
        e = FakeEntity(self._log, kind)
        self.entities.append(e)
        return e

    def GetDefaultEntity(self, kind):
        return ("default", kind)


class FakeDoc(Rec):
    def __init__(self, log):
        super().__init__(log, "doc")
        self.__dict__["part"] = FakePart(log)

    def GetPart(self, which):
        assert which == c.pTop_Part
        return self.part


class FakeKompas(Rec):
    def __init__(self):
        super().__init__([], "kompas")
        self.__dict__["doc"] = FakeDoc(self._log)

    def Document3D(self):
        return self.doc


@pytest.fixture
def built():
    fake = FakeKompas()
    doc = KompasApp(kompas=fake).new_part("Цилиндр")
    build(doc.builder)
    return fake, doc


def test_body_profile_is_closed_and_on_circle():
    p = CylinderParams()
    segs = body_profile(p)
    assert len(segs) == 8
    for (_, (_, end)), (_, (start, _)) in zip(segs, segs[1:] + segs[:1]):
        assert end == pytest.approx(start)
    for kind, pts in segs:
        if kind == "arc":
            for x, y in pts:
                assert math.hypot(x, y) == pytest.approx(p.body_d / 2)


def test_build_sequence(built):
    fake, doc = built
    kinds = [e.kind for e in fake.doc.part.entities]
    assert kinds.count(c.o3d_baseExtrusion) == 1
    assert kinds.index(c.o3d_baseExtrusion) < kinds.index(c.o3d_bossExtrusion)
    # body + 2 flanges + boss + lugs + pads
    assert kinds.count(c.o3d_bossExtrusion) == 5
    # bore + 2 steps + 2×(flange holes, M62) + 2 windows + Ø260 + Ø168 + 2 M16 + 2 M36
    assert kinds.count(c.o3d_cutExtrusion) == 15
    assert doc.part.props["name"] == "Цилиндр"


def test_hole_counts(built):
    fake, _ = built
    circles = [a for n, m, a in fake._log if n == "doc2d" and m == "ksCircle"]
    radii = [round(a[2] * 2, 2) for a in circles]
    assert radii.count(17.5) == 32  # 16 × M20 on each end
    assert radii.count(234.0) == 8  # 4 windows × 2 sketches (each cut both sides)
    assert radii.count(32.0) == 4 * 2
    assert radii.count(14.0) == 4 * 2


def test_body_length_and_cut_flags(built):
    fake, _ = built
    base = next(e for e in fake.doc.part.entities if e.kind == c.o3d_baseExtrusion)
    assert base.definition.props["directionType"] == c.dtMiddlePlane
    assert ("def24", "SetSideParam", (True, c.etBlind, 850.0, 0, False)) in fake._log
    for e in fake.doc.part.entities:
        if e.kind == c.o3d_cutExtrusion:
            assert e.definition.props["cut"] is True


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        build(KompasApp(kompas=FakeKompas()).new_part().builder, CylinderParams(window_offset=300))
