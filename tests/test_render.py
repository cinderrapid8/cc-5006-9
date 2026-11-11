"""Final render: frame accuracy, output quality parameters, originals-only rule."""

import os

import pytest

from proxyworkstation.core.errors import MissingSourceError
from proxyworkstation.core.ffutils import extract_frame, probe
from proxyworkstation.core.verification import _frame_mae, _load_gray


def _build_timeline(ws):
    ws.add_clip("SHOT_A", 2, 10)
    ws.add_clip("SHOT_B", 5, 12)
    ws.add_clip("SHOT_C", 0, 6)
    return 8 + 7 + 6  # expected total frames


def test_render_frame_count_matches_sum_of_intervals(proxied):
    ws = proxied["ws"]
    expected = _build_timeline(ws)
    out = os.path.join(proxied["tmp"], "final.mp4")
    info = ws.render_final(out, crf=18, preset="medium")
    assert info["frame_accurate"] is True
    assert info["actual_frame_count"] == expected
    meta = probe(out)
    assert meta["frame_count"] == expected
    assert meta["width"] == 960 and meta["height"] == 540  # first source native


def test_render_respects_custom_quality_parameters(proxied):
    ws = proxied["ws"]
    _build_timeline(ws)
    out = os.path.join(proxied["tmp"], "final_hd.mp4")
    info = ws.render_final(out, width=1280, height=720, fps=24.0, crf=12, preset="slow")
    meta = probe(out)
    assert (meta["width"], meta["height"]) == (1280, 720)
    assert abs(meta["fps"] - 24.0) < 0.01
    assert info["crf"] == 12 and info["preset"] == "slow"


def test_rendered_frames_correspond_to_exact_source_frames(proxied, tmp_path):
    """Program frame 0 must equal SHOT_A source frame 2, proving relink accuracy."""
    ws = proxied["ws"]
    _build_timeline(ws)
    out = os.path.join(proxied["tmp"], "final_check.mp4")
    ws.render_final(out)
    source_a = ws.store.get_source_by_shot("SHOT_A")
    source_b = ws.store.get_source_by_shot("SHOT_B")

    out_first = str(tmp_path / "out0.png")
    src_frame2 = str(tmp_path / "src2.png")
    extract_frame(out, 0, out_first, 24.0)
    extract_frame(source_a["current_path"], 2, src_frame2, 24.0)
    mae_same = _frame_mae(
        _load_gray(out_first, (480, 270)), _load_gray(src_frame2, (480, 270))
    )
    assert mae_same < 6.0

    # Boundary: program frame 8 is the first frame of the SHOT_B segment (= src 5).
    out_boundary = str(tmp_path / "out8.png")
    src_b5 = str(tmp_path / "srcb5.png")
    extract_frame(out, 8, out_boundary, 24.0)
    extract_frame(source_b["current_path"], 5, src_b5, 24.0)
    mae_boundary = _frame_mae(
        _load_gray(out_boundary, (480, 270)), _load_gray(src_b5, (480, 270))
    )
    assert mae_boundary < 6.0


def test_render_uses_original_resolution_not_proxy(proxied):
    ws = proxied["ws"]
    _build_timeline(ws)
    source = ws.store.get_source_by_shot("SHOT_B")
    record = ws.store.get_proxy_for(source["content_hash"])
    assert probe(record["proxy_path"])["height"] == 270  # proxy is low-res
    out = os.path.join(proxied["tmp"], "fullres.mp4")
    ws.render_final(out)
    assert probe(out)["height"] == 540  # output follows the original timeline base


def test_empty_timeline_does_not_render(workspace):
    ws = workspace["ws"]
    with pytest.raises(IndexError):
        ws.render_final(os.path.join(workspace["tmp"], "none.mp4"))


def test_render_blocked_lists_every_missing_source(proxied):
    import shutil

    ws = proxied["ws"]
    _build_timeline(ws)
    moved = os.path.join(proxied["tmp"], "gone")
    os.makedirs(moved, exist_ok=True)
    for source in ws.store.list_sources():
        shutil.move(
            source["current_path"],
            os.path.join(moved, os.path.basename(source["current_path"])),
        )
    with pytest.raises(MissingSourceError) as exc:
        ws.render_final(os.path.join(proxied["tmp"], "x.mp4"))
    assert {m["shot_id"] for m in exc.value.missing} == {
        "SHOT_A",
        "SHOT_B",
        "SHOT_C",
    }
