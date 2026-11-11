"""Frame mapping: time-preserving proxies round-trip; other transforms rejected."""

import pytest

from proxyworkstation.core.errors import UnsupportedTransformError
from proxyworkstation.core.ffutils import probe
from proxyworkstation.core.framemap import FrameMap, build_time_preserving_map


def _meta(**overrides):
    base = {"fps": 24.0, "frame_count": 24, "width": 960, "height": 540}
    base.update(overrides)
    return base


def test_time_preserving_map_round_trips_any_frame():
    source = _meta()
    proxy = _meta(width=480, height=270)
    fmap = build_time_preserving_map(source, proxy)
    assert fmap.source_start_offset == 0
    for n in (0, 1, 13, 23):
        assert fmap.round_trip(n) == n
        assert fmap.to_proxy_frame(n) == n
        assert fmap.to_source_frame(n) == n


def test_framerate_change_is_rejected():
    with pytest.raises(UnsupportedTransformError, match="frame-rate"):
        build_time_preserving_map(_meta(), _meta(fps=30.0))


def test_frame_count_change_is_rejected():
    with pytest.raises(UnsupportedTransformError, match="frame-count"):
        build_time_preserving_map(_meta(), _meta(frame_count=20))


def test_map_persists_and_restores():
    fmap = build_time_preserving_map(_meta(), _meta(width=480, height=270))
    restored = FrameMap.from_dict(fmap.to_dict())
    assert restored.round_trip(7) == 7
    assert restored.proxy_frame_count == 24


def test_generated_proxy_satisfies_mapping_contract(proxied):
    ws = proxied["ws"]
    for shot in ("SHOT_A", "SHOT_B", "SHOT_C"):
        source = ws.store.get_source_by_shot(shot)
        record = ws.store.get_proxy_for(source["content_hash"])
        fmap = record["frame_map"]
        assert fmap["source_fps"] == fmap["proxy_fps"]
        assert fmap["source_frame_count"] == fmap["proxy_frame_count"]
        assert fmap["source_start_offset"] == 0
        src_meta = probe(source["current_path"])
        prx_meta = probe(record["proxy_path"])
        assert prx_meta["height"] < src_meta["height"]
        assert prx_meta["frame_count"] == src_meta["frame_count"]


def test_upscale_proxy_request_is_rejected(workspace):
    ws = workspace["ws"]
    with pytest.raises(UnsupportedTransformError, match="not lower"):
        ws.generate_proxy("SHOT_A", workspace["proxy_dir"], height=1080)


def test_out_of_range_frame_translation_raises():
    fmap = build_time_preserving_map(_meta(), _meta())
    with pytest.raises(ValueError):
        fmap.to_proxy_frame(24)
    with pytest.raises(ValueError):
        fmap.to_source_frame(-1)
