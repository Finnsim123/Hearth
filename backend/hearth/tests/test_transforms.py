"""Transform whitelist registry — the safety boundary's metadata (Step 3 §d)."""
from __future__ import annotations

from hearth.domain.features import transforms as T
from hearth.domain.schemas import InfoTier


def test_registry_is_well_formed():
    reg = T.all_transforms()
    assert reg, "registry must not be empty"
    valid_tiers = {t.value for t in InfoTier} | {T.COMPOSITE}
    for tid, spec in reg.items():
        assert spec.id == tid
        assert spec.input_kind in (T.ENTITY, T.FEATURE)
        assert spec.tiers and spec.tiers <= valid_tiers
        # composites take feature inputs; per-entity transforms take entity inputs
        if T.COMPOSITE in spec.tiers:
            assert spec.input_kind == T.FEATURE
        else:
            assert spec.input_kind == T.ENTITY
        for token in spec.params.values():
            assert token in T._TYPE_TOKENS


def test_conservative_is_a_subset_of_full():
    full = T.whitelist_ids("full")
    cons = T.whitelist_ids("conservative")
    assert cons < full                      # strictly smaller
    assert T.whitelist_ids("nonsense") == cons   # unknown mode -> conservative
    # the recipe-equivalent basics must be in the conservative set
    for tid in ("occupancy_fraction", "window_mean", "window_delta", "co_occurrence_and"):
        assert tid in cons
    # a richer transform must be full-only
    assert "window_slope" in full and "window_slope" not in cons


def test_t4_counter_excludes_raw_value_transforms():
    # cumulative counters must only offer rate/delta, never mean/max (raw value)
    for tid, spec in T.all_transforms().items():
        if InfoTier.CUMULATIVE_COUNTER.value in spec.tiers:
            assert tid in ("counter_rate", "window_delta")


def test_check_params():
    tsl = T.get_transform("time_since_last_change")     # {cap_min: int}
    assert T.check_params(tsl, {"cap_min": 240})
    assert not T.check_params(tsl, {})                  # missing required
    assert not T.check_params(tsl, {"cap_min": 2.5})    # wrong type (float != int)
    assert not T.check_params(tsl, {"cap_min": 240, "x": 1})  # unknown param

    mean = T.get_transform("window_mean")               # no params
    assert T.check_params(mean, {})
    assert T.check_params(mean, None)
    assert not T.check_params(mean, {"foo": 1})

    onehot = T.get_transform("state_onehot")            # {states: list[str]}
    assert T.check_params(onehot, {"states": ["playing", "paused"]})
    assert not T.check_params(onehot, {"states": [1, 2]})
    assert not T.check_params(onehot, {"states": "playing"})
