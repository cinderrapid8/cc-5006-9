"""Content-hash identity: same filename never drives replacement."""

import os
import shutil

import pytest

from proxyworkstation.core.identity import sha256_file, verify_hash
from scripts.make_samples import make_clip


def test_different_content_has_different_identity(tmp_path):
    p1 = str(tmp_path / "clip.mp4")
    p2 = str(tmp_path / "sub" / "clip.mp4")
    make_clip("SHOT_A", p1, 640, 360, frame_count=12)
    make_clip("SHOT_B", p2, 640, 360, frame_count=12)
    assert os.path.basename(p1) == os.path.basename(p2)
    assert sha256_file(p1) != sha256_file(p2)


def test_identical_bytes_same_identity_even_when_renamed(tmp_path, workspace):
    src = workspace["paths"]["SHOT_A"]
    renamed = str(tmp_path / "totally_different_name.mp4")
    import shutil

    shutil.copy2(src, renamed)
    assert sha256_file(src) == sha256_file(renamed)
    ok, actual = verify_hash(renamed, sha256_file(src))
    assert ok and actual == sha256_file(src)


def test_reimport_moved_file_links_same_logical_shot(workspace, tmp_path):
    ws = workspace["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    archive = tmp_path / "archive"
    archive.mkdir()
    new_location = str(archive / "SHOT_A.mp4")
    shutil.copy2(source["current_path"], new_location)
    result = ws.import_source(new_location, "SHOT_A")
    assert result["already_known"] is True
    again = ws.store.get_source_by_shot("SHOT_A")
    assert again["content_hash"] == source["content_hash"]


def test_same_named_decoy_cannot_relink(workspace, tmp_path):
    ws = workspace["ws"]
    target = ws.store.get_source_by_shot("SHOT_A")
    decoy = str(tmp_path / "SHOT_A.mp4")
    make_clip("SHOT_A", decoy, 960, 540, frame_count=24, tag="DECOY")
    with pytest.raises(Exception):
        ws.relocate_source(target["content_hash"], decoy)
    # Original record must remain untouched.
    assert ws.store.get_source_by_hash(target["content_hash"])["current_path"] == (
        target["current_path"]
    )
