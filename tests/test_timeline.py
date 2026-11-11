"""Timeline semantics: clips bind logical shots + source-frame intervals."""

import pytest

from proxyworkstation.core.errors import WorkstationError


def test_clips_store_source_frame_coordinates(proxied):
    ws = proxied["ws"]
    clip_id = ws.add_clip("SHOT_A", 3, 11)
    clip = next(c for c in ws.store.list_clips() if c["clip_id"] == clip_id)
    assert clip["source_in"] == 3
    assert clip["source_out"] == 11
    assert clip["shot_id"] == "SHOT_A"


def test_preview_quality_switch_keeps_inout_points(proxied):
    ws = proxied["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    ws.add_clip("SHOT_A", 4, 12)
    before = [(c["source_in"], c["source_out"]) for c in ws.store.list_clips()]

    proxy_media = ws.resolve_media(source["content_hash"], "proxy")
    original_media = ws.resolve_media(source["content_hash"], "original")
    assert proxy_media["kind"] == "proxy"
    assert original_media["kind"] == "original"
    assert proxy_media["path"] != original_media["path"]

    after = [(c["source_in"], c["source_out"]) for c in ws.store.list_clips()]
    assert before == after == [(4, 12)]


def test_interval_bounds_validated_against_source(workspace):
    ws = workspace["ws"]
    with pytest.raises(ValueError):
        ws.add_clip("SHOT_A", 0, 25)  # SHOT_A only has 24 frames
    with pytest.raises(ValueError):
        ws.add_clip("SHOT_A", 10, 10)  # empty interval
    with pytest.raises(WorkstationError):
        ws.add_clip("UNKNOWN", 0, 5)


def test_remove_clip(proxied):
    ws = proxied["ws"]
    cid = ws.add_clip("SHOT_C", 1, 4)
    assert any(c["clip_id"] == cid for c in ws.store.list_clips())
    ws.store.remove_clip(cid)
    assert not any(c["clip_id"] == cid for c in ws.store.list_clips())


def test_proxy_resolution_falls_back_but_original_never_uses_proxy(workspace):
    ws = workspace["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    h = source["content_hash"]
    # No proxy yet: proxy request falls back to original for editing.
    fallback = ws.resolve_media(h, "proxy")
    assert fallback["kind"] == "original-fallback"
    original = ws.resolve_media(h, "original")
    assert original["kind"] == "original"
    assert original["proxy_available"] is False
