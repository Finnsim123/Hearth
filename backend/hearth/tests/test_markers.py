from __future__ import annotations

import pandas as pd

from hearth.domain.markers import (
    Marker,
    apply_marker_prior,
    binding_from_feature,
    load_markers,
    looks_like_marker,
    marker_fired,
    markers_for,
    save_markers,
)


class FakeRepo:
    def __init__(self): self._s = {}
    def get_setting(self, k, d=None): return self._s.get(k, d)
    def set_setting(self, k, v): self._s[k] = v


def test_binding_from_feature_strips_known_suffixes():
    assert binding_from_feature("coffee_delta") == "coffee"
    assert binding_from_feature("lamp_on_frac") == "lamp"
    assert binding_from_feature("alice_loc_on_last") == "alice_loc"   # underscore in name
    assert binding_from_feature("weird") == "weird"


def test_marker_fired_on_edge_and_delta():
    m = Marker(slug="coffee", name="Coffee", to_state="home", binding_name="coffee")
    assert marker_fired(pd.Series({"coffee_delta": 12.0}), m)
    assert marker_fired(pd.Series({"coffee_on_frac": 0.8}), m)
    assert not marker_fired(pd.Series({"coffee_delta": 0.0}), m)
    assert not marker_fired(pd.Series({"other_delta": 5.0}), m)


def test_apply_prior_boosts_to_state_and_damps_self_loop():
    row = pd.Series({"asleep": 0.7, "home": 0.2, "cooking": 0.1})
    m = Marker(slug="wake", name="Wake", from_state="asleep", to_state="home",
               binding_name="coffee")
    out = apply_marker_prior(row, "asleep", [m])
    assert out.idxmax() == "home"                 # flipped at the boundary
    assert out["home"] > out["asleep"]
    assert abs(float(out.sum()) - 1.0) < 1e-9     # renormalised


def test_prior_noop_when_from_state_mismatches():
    row = pd.Series({"asleep": 0.7, "home": 0.3})
    m = Marker(slug="wake", name="Wake", from_state="asleep", to_state="home",
               binding_name="coffee")
    out = apply_marker_prior(row, "home", [m])     # prev != from
    assert out.idxmax() == "asleep" and out.equals(row)


def test_from_none_anchors_any_previous_state():
    row = pd.Series({"cooking": 0.6, "home": 0.4})
    m = Marker(slug="x", name="x", from_state=None, to_state="home", binding_name="b")
    out = apply_marker_prior(row, "cooking", [m])
    assert out.idxmax() == "home"


def test_save_load_and_person_scope():
    repo = FakeRepo()
    save_markers(repo, [
        Marker(slug="a", name="A", to_state="home", binding_name="x", person_id="alice"),
        Marker(slug="b", name="B", to_state="home", binding_name="y", enabled=False),
    ])
    assert [m.slug for m in load_markers(repo)] == ["a", "b"]
    assert [m.slug for m in markers_for(repo, "alice")] == ["a"]   # enabled + scope


def test_looks_like_marker_heuristic():
    spike = [0] * 24; spike[7] = 5                 # all at 07:00
    assert looks_like_marker(spike, n_windows=5)
    flat = [3] * 24                                # spread all day, many windows
    assert not looks_like_marker(flat, n_windows=72)
