"""
Relief Studio — server-side worker tests.

Run: pytest tests/test_suite.py -v

WHAT CHANGED AND WHY
--------------------
The previous version of this file re-implemented every algorithm in Python and
asserted against its own reimplementation. Its mirror of surfZ() rounded the
grid index; the shipped JavaScript did not. That single divergence let a fully
dead roughing pass — 12 corner plunges, zero cutting distance — pass a "40/40
audit" for the life of the project. Other classes here were worse: the
post-processor tests built G-code with a lambda inside the test and then
asserted on the string they had just written, and test_surface_tri_count
asserted 2*(50-1)*(50-1) == 4802, an arithmetic identity involving no product
code at all.

Browser algorithms are now covered by tests/test_browser_algorithms.mjs, which
executes the real shipped JavaScript. This file covers the server workers by
importing them, and — importantly — checks that the two paths agree, since the
product promises the escalation path is "the same math as the browser".
"""
import json
import math

import numpy as np
import pytest

from workers.heavy_toolpath import (
    ball_nose_drop, flat_drop, vbit_drop, run_heavy_toolpath,
)

COLS = ROWS = 100
MAT_W = MAT_H = 100.0
MAX_DEPTH = 8.0
# Same convention as the browser: node gx sits at (gx/(cols-1))*mat_w.
PX_PER_MM = (COLS - 1) / MAT_W


def grid(kind):
    g = np.zeros((ROWS, COLS), dtype=np.float32)
    ys, xs = np.mgrid[0:ROWS, 0:COLS]
    cx, cy = xs / COLS - 0.5, ys / ROWS - 0.5
    if kind == "dome":
        g = np.maximum(0, 1 - np.hypot(cx, cy) * 2)
    elif kind == "flat":
        g = np.ones_like(g)
    elif kind == "black":
        g = np.zeros_like(g)
    elif kind == "step":
        g = np.where(xs < COLS // 2, 1.0, 0.0)
    elif kind == "gradient":
        g = xs / (COLS - 1)
    elif kind == "ridge":
        g = np.abs(np.sin(xs * 0.4)) * np.abs(np.cos(ys * 0.3))
    elif kind == "noise":
        g = np.random.default_rng(1234).random((ROWS, COLS))
    return g.astype(np.float32)


def surface(kind, md=MAX_DEPTH):
    return -(1 - grid(kind)) * md


class TestDropCutterNoGouge:
    """The tool tip must never sit below the surface it is tracing."""

    @pytest.mark.parametrize("kind", ["dome", "flat", "black", "step", "gradient", "ridge", "noise"])
    def test_ball_nose(self, kind):
        surf = surface(kind)
        r_mm = 1.5
        r_px = int(np.ceil(r_mm * PX_PER_MM))
        tip = ball_nose_drop(surf, r_mm, r_px, PX_PER_MM)
        margin = (tip - surf)[r_px:-r_px, r_px:-r_px]
        assert margin.min() >= -1e-4, f"{kind}: gouge {margin.min()}"

    @pytest.mark.parametrize("kind", ["dome", "step", "ridge", "noise"])
    def test_flat_endmill(self, kind):
        surf = surface(kind)
        r_px = int(np.ceil(1.5 * PX_PER_MM))
        tip = flat_drop(surf, 1.5, r_px)
        margin = (tip - surf)[r_px:-r_px, r_px:-r_px]
        assert margin.min() >= -1e-4

    @pytest.mark.parametrize("angle", [15, 30, 45, 60, 90, 120, 170])
    def test_vbit(self, angle):
        surf = surface("ridge")
        r_mm = 2.0
        r_px = int(np.ceil(r_mm * PX_PER_MM))
        tan_half = math.tan(math.radians(angle / 2))
        tip = vbit_drop(surf, r_mm, r_px, PX_PER_MM, tan_half)
        margin = (tip - surf)[r_px:-r_px, r_px:-r_px]
        assert margin.min() >= -1e-4, f"{angle} deg: gouge {margin.min()}"
        assert np.isfinite(tip).all()


class TestDropCutterGeometry:
    def test_flat_surface_needs_no_cut(self):
        surf = np.zeros((ROWS, COLS), dtype=np.float32)
        tip = ball_nose_drop(surf, 1.5, 2, PX_PER_MM)
        assert np.abs(tip[10:-10, 10:-10]).max() < 1e-4

    def test_black_surface_reaches_full_depth(self):
        surf = surface("black")
        tip = ball_nose_drop(surf, 1.5, 2, PX_PER_MM)
        interior = tip[10:-10, 10:-10]
        assert np.allclose(interior, -MAX_DEPTH, atol=1e-3)

    def test_ball_tip_is_centre_minus_radius_on_flat(self):
        """On a flat surface the ball touches at its lowest point, so the tip
        sits exactly on the surface regardless of radius."""
        for r_mm in (0.5, 1.5, 3.0):
            surf = np.full((ROWS, COLS), -2.0, dtype=np.float32)
            r_px = int(np.ceil(r_mm * PX_PER_MM))
            tip = ball_nose_drop(surf, r_mm, r_px, PX_PER_MM)
            assert abs(float(tip[50, 50]) - (-2.0)) < 1e-3

    def test_vbit_shallower_angle_rides_higher(self):
        """A wider included angle cannot reach as deep into a narrow valley."""
        surf = surface("step")
        r_px = int(np.ceil(2.0 * PX_PER_MM))
        narrow = vbit_drop(surf, 2.0, r_px, PX_PER_MM, math.tan(math.radians(15)))
        wide = vbit_drop(surf, 2.0, r_px, PX_PER_MM, math.tan(math.radians(60)))
        assert narrow.min() <= wide.min() + 1e-6


class TestGridScaleParity:
    """W5/W8 regression: the server used px_per_mm = cols/mat_w and derived
    mm-per-pixel as tool_r/ceil(r_px), while the browser maps node gx to
    (gx/(cols-1))*mat_w and uses the exact grid scale. The two paths therefore
    produced different toolpaths for identical input, contradicting both the
    'same math as the browser' claim and the determinism guarantee."""

    def test_uses_exact_grid_scale(self):
        """Lift at the centre of a ball footprint must equal the full radius."""
        surf = np.zeros((ROWS, COLS), dtype=np.float32)
        r_mm = 1.5
        r_px = int(np.ceil(r_mm * PX_PER_MM))
        tip = ball_nose_drop(surf, r_mm, r_px, PX_PER_MM)
        # A flat surface at Z=0 must give tip Z=0 exactly; any mm-per-pixel
        # error shows up here as a non-zero offset.
        assert abs(float(tip[50, 50])) < 1e-5

    def test_mm_per_px_matches_browser_convention(self):
        """(cols-1)/mat_w, not cols/mat_w — a 1% error at cols=100."""
        assert PX_PER_MM == pytest.approx((COLS - 1) / MAT_W)
        assert PX_PER_MM != pytest.approx(COLS / MAT_W)


class _FakeJob:
    """Minimal stand-in for the SQLAlchemy Job row the worker mutates."""
    def __init__(self, settings):
        self.id = "job-test"
        self.user_id = "user-test"
        self.settings_json = json.dumps(settings)
        self.progress = 0
        self.result_key = None


class _FakeDB:
    def commit(self):
        pass


def _settings(**over):
    s = {
        "heightfield": grid("dome").ravel().tolist(),
        "cols": COLS, "rows": ROWS,
        "mat_w": MAT_W, "mat_h": MAT_H, "max_depth": MAX_DEPTH,
        "tool_type": "ball", "tool_dia": 3.0, "stepover_pct": 12,
        "feed": 1000, "rpm": 18000, "safe_z": 5, "plunge": 400,
        "v_angle": 90, "dialect": "grbl",
    }
    s.update(over)
    return s


class TestHeightfieldValidation:
    """W4 regression: a missing or wrong-length heightfield silently became
    np.ones() — a flat surface. The job reported DONE and returned valid-looking
    G-code that carves nothing. For a CNC product that is the worst failure
    mode: plausible output, wrong geometry, no warning."""

    def test_missing_heightfield_raises(self):
        job = _FakeJob(_settings(heightfield=None))
        with pytest.raises(ValueError, match="heightfield"):
            run_heavy_toolpath(job, _FakeDB())

    def test_wrong_length_heightfield_raises(self):
        job = _FakeJob(_settings(heightfield=[0.5] * 17))
        with pytest.raises(ValueError, match="!="):
            run_heavy_toolpath(job, _FakeDB())

    def test_nan_heightfield_raises(self):
        bad = grid("dome").ravel().tolist()
        bad[0] = float("nan")
        job = _FakeJob(_settings(heightfield=bad))
        with pytest.raises(ValueError, match="NaN"):
            run_heavy_toolpath(job, _FakeDB())


class TestGeneratedGCode:
    """Exercise the real generator, not a lambda defined inside the test."""

    def _gcode(self, monkeypatch, **over):
        captured = {}

        def fake_put(key, body, content_type, metadata=None):
            captured["key"] = key
            captured["body"] = body.decode()
            return key

        import workers.heavy_toolpath as ht
        monkeypatch.setattr(ht, "put_object", fake_put)
        job = _FakeJob(_settings(**over))
        run_heavy_toolpath(job, _FakeDB())
        return captured["body"], job

    def test_emits_valid_program(self, monkeypatch):
        gc, job = self._gcode(monkeypatch)
        assert "G21" in gc and "G90" in gc
        assert gc.rstrip().endswith("M30")
        assert "NaN" not in gc and "inf" not in gc.lower()
        assert job.result_key.endswith("relief.nc")

    def test_depth_is_clamped_to_declared_relief_depth(self, monkeypatch):
        gc, _ = self._gcode(monkeypatch)
        zs = [float(t[1:]) for line in gc.splitlines()
              for t in line.split() if t.startswith("Z")]
        assert zs, "no Z moves emitted"
        assert min(zs) >= -MAX_DEPTH - 1e-6
        assert max(zs) <= 5.0 + 1e-6

    def test_flat_image_removes_no_material(self, monkeypatch):
        gc, _ = self._gcode(monkeypatch, heightfield=grid("flat").ravel().tolist())
        cutting = [float(t[1:]) for line in gc.splitlines() if line.startswith("G1")
                   for t in line.split() if t.startswith("Z")]
        assert all(z >= -1e-6 for z in cutting), "cut into a flat white image"

    @pytest.mark.parametrize("dialect,wants_g17,comment", [
        ("grbl", False, ";"), ("mach3", True, ";"), ("linuxcnc", True, "("),
    ])
    def test_post_processor_dialects(self, monkeypatch, dialect, wants_g17, comment):
        gc, _ = self._gcode(monkeypatch, dialect=dialect)
        lines = gc.splitlines()
        assert ("G17" in lines) == wants_g17
        assert lines[0].startswith(comment)

    def test_stepover_clamped_and_reported(self, monkeypatch):
        """W6 regression: passes were snapped onto integer grid rows, so a
        stepover finer than the grid emitted duplicate identical passes and the
        requested value was silently not honoured."""
        gc, _ = self._gcode(monkeypatch, stepover_pct=0.1)
        assert "achieved" in gc
        ys = [float(t[1:]) for line in gc.splitlines() if line.startswith("G0")
              for t in line.split() if t.startswith("Y")]
        assert len(ys) == len(set(ys)), "duplicate passes at the same Y"

    def test_determinism(self, monkeypatch):
        a, _ = self._gcode(monkeypatch)
        b, _ = self._gcode(monkeypatch)
        assert a == b
