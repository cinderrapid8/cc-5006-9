"""Offline originals, relocation and the no-proxy-substitution render rule."""

import os
import shutil

import pytest

from proxyworkstation.core.errors import (
    CorruptProxyError,
    MissingSourceError,
    WorkstationError,
)
from proxyworkstation.core.identity import sha256_file
from scripts.make_samples import make_clip


def _take_offline(workspace):
    ws = workspace["ws"]
    os.makedirs(workspace["moved_dir"], exist_ok=True)
    locations = {}
    for source in ws.store.list_sources():
        target = os.path.join(
            workspace["moved_dir"], os.path.basename(source["current_path"])
        )
        shutil.move(source["current_path"], target)
        locations[source["shot_id"]] = target
    return locations


def test_source_status_transitions(proxied):
    ws = proxied["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    assert ws.store.source_status(source) == "online"
    original = source["current_path"]
    shutil.move(original, original + ".moved")
    assert ws.store.source_status(ws.store.get_source_by_shot("SHOT_A")) == "offline"
    shutil.move(original + ".moved", original)
    assert ws.store.source_status(ws.store.get_source_by_shot("SHOT_A")) == "online"


def test_offline_original_keeps_proxy_editing(proxied):
    ws = proxied["ws"]
    _take_offline(proxied)
    source = ws.store.get_source_by_shot("SHOT_A")
    media = ws.resolve_media(source["content_hash"], "proxy")
    assert media["online"] is True
    assert media["kind"] == "proxy"
    assert os.path.isfile(media["path"])
    # Timeline edits still work.
    clip_id = ws.add_clip("SHOT_A", 1, 5)
    assert clip_id > 0


def test_render_blocked_when_original_offline(proxied):
    ws = proxied["ws"]
    ws.add_clip("SHOT_A", 2, 8)
    _take_offline(proxied)
    check = ws.preflight()
    assert check["ready"] is False
    assert {m["shot_id"] for m in check["missing"]} == {"SHOT_A"}
    out = os.path.join(proxied["tmp"], "blocked.mp4")
    with pytest.raises(MissingSourceError) as exc:
        ws.render_final(out)
    assert "SHOT_A" in str(exc.value)
    assert not os.path.exists(out)  # nothing low-res was silently written


def test_relocation_by_hash_restores_online_state(proxied):
    ws = proxied["ws"]
    locations = _take_offline(proxied)
    for source in ws.store.list_sources():
        ws.relocate_source(source["content_hash"], locations[source["shot_id"]])
        refreshed = ws.store.get_source_by_hash(source["content_hash"])
        assert ws.store.source_status(refreshed) == "online"
    assert ws.preflight()["ready"] is True


def test_relocation_rejects_same_name_different_content(proxied):
    ws = proxied["ws"]
    source = ws.store.get_source_by_shot("SHOT_B")
    decoy = os.path.join(proxied["tmp"], "SHOT_B.mp4")
    make_clip("SHOT_B", decoy, 1280, 720, frame_count=30, tag="DIFFERENT")
    assert sha256_file(decoy) != source["content_hash"]
    with pytest.raises(WorkstationError, match="content mismatch"):
        ws.relocate_source(source["content_hash"], decoy)


def test_auto_relocate_finds_hash_match_in_folder(proxied):
    ws = proxied["ws"]
    locations = _take_offline(proxied)
    source = ws.store.get_source_by_shot("SHOT_C")
    # Nest the file two levels deep with a changed name.
    deep = os.path.join(proxied["moved_dir"], "nested", "renamed_shot_c.mp4")
    os.makedirs(os.path.dirname(deep), exist_ok=True)
    shutil.move(locations["SHOT_C"], deep)
    found = ws.auto_relocate(source["content_hash"], [proxied["moved_dir"]])
    assert found is not None
    refreshed = ws.store.get_source_by_hash(source["content_hash"])
    assert ws.store.source_status(refreshed) == "online"


def test_replaced_file_detected_as_moved_content(proxied):
    ws = proxied["ws"]
    ws.add_clip("SHOT_A", 0, 4)
    source = ws.store.get_source_by_shot("SHOT_A")
    original_path = source["current_path"]
    decoy = os.path.join(proxied["tmp"], "replacement.mp4")
    make_clip("SHOT_A", decoy, 960, 540, frame_count=24, tag="REPLACED")
    os.remove(original_path)
    shutil.copy2(decoy, original_path)  # same path/name, different bytes
    refreshed = ws.store.get_source_by_shot("SHOT_A")
    status = ws.store.source_status(refreshed)
    assert status == "moved-content"
    check = ws.preflight()
    assert any(m["shot_id"] == "SHOT_A" for m in check["missing"])


def test_proxy_relocation_requires_hash_match(proxied):
    ws = proxied["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    record = ws.store.get_proxy_for(source["content_hash"])
    new_home = os.path.join(proxied["tmp"], "proxy_moved.mp4")
    shutil.move(record["proxy_path"], new_home)
    ws.relocate_proxy(record["proxy_hash"], new_home)
    with pytest.raises(CorruptProxyError):
        ws.relocate_proxy(record["proxy_hash"], source["current_path"])
