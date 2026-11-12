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

    def array(self):
        return EdgeArray(self.__dict__.setdefault("edges", []))


class EdgeArray:
    def __init__(self, items):
        self.items = items

    def Add(self, item):
        self.items.append(item)
        return True


class FakeEdges:
    def GetCount(self):
        return 3

    def GetByIndex(self, i):
        return ("edge", i)


class FakeEntity:
    def __init__(self, log, kind, ok=True):
        self.kind, self.definition, self.ok = kind, FakeDef(log, kind), ok

    def GetDefinition(self):
        return self.definition

    def Create(self):
        return self.ok


class FakePart(Rec):
    def __init__(self, log, fillet_ok=True):
        super().__init__(log, "part")
        self.__dict__["entities"] = []
        self.__dict__["fillet_ok"] = fillet_ok

    def NewEntity(self, kind):
        e = FakeEntity(self._log, kind, ok=self.fillet_ok or kind != c.o3d_fillet)
        self.entities.append(e)
        return e

    def EntityCollection(self, kind):
        assert kind == c.o3d_edge
        return FakeEdges()

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
    # 2 flanges + 2 R50 boss pairs + boss + lugs + pads (body is the base extrusion)
    assert kinds.count(c.o3d_bossExtrusion) == 7
    # fillet after all bosses, before the first cut
    fillet = kinds.index(c.o3d_fillet)
    assert max(i for i, k in enumerate(kinds) if k == c.o3d_bossExtrusion) < fillet
    assert fillet < kinds.index(c.o3d_cutExtrusion)
    f = fake.doc.part.entities[fillet].definition
    assert f.props["radius"] == 2.0 and f.edges == [("edge", 0), ("edge", 1), ("edge", 2)]
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


def test_flange_hole_angles_match_drawing(built):
    fake, _ = built
    centres = [(a[0], a[1]) for n, m, a in fake._log
               if n == "doc2d" and m == "ksCircle" and round(a[2] * 2, 2) == 17.5][:16]
    # measured from the vertical axis: ±10°, ±30° in the top half (drawing dims)
    from_vertical = sorted(round(math.degrees(math.atan2(abs(x), abs(y))), 6) for x, y in centres)
    assert from_vertical == [10.0] * 4 + [30.0] * 4 + [60.0] * 4 + [80.0] * 4
    for x, y in centres:
        assert math.hypot(x, y) == pytest.approx(251.5)


def test_m62_bosses(built):
    fake, _ = built
    bosses = [a for n, m, a in fake._log if n == "doc2d" and m == "ksCircle" and a[2] == 50.0]
    assert sorted((x, y) for x, y, *_ in bosses) == [(-315.0, 0)] * 2 + [(315.0, 0)] * 2


def test_failed_fillet_does_not_abort(capsys):
    fake = FakeKompas()
    fake.doc.__dict__["part"] = FakePart(fake._log, fillet_ok=False)
    build(KompasApp(kompas=fake).new_part().builder)
    assert "fillets could not be built" in capsys.readouterr().out
    assert any(e.kind == c.o3d_cutExtrusion for e in fake.doc.part.entities)


def test_fillets_can_be_disabled():
    fake = FakeKompas()
    build(KompasApp(kompas=fake).new_part().builder, CylinderParams(fillet_r=0))
    assert all(e.kind != c.o3d_fillet for e in fake.doc.part.entities)


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        build(KompasApp(kompas=FakeKompas()).new_part().builder, CylinderParams(window_offset=300))
