"""Experimental proxy-frame association: both schemes, residuals and wiring.

These tests use the same synthetic shot generator as the rest of the suite and
construct re-encode variants with an explicit ground-truth source-index list,
so every residual is measured against known correspondence rather than assumed.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from proxyworkstation.core.ffutils import make_proxy, probe
from proxyworkstation.experimental import (
    ContentAnchorResolver,
    FrameResolution,
    TimeMappingResolver,
    build_content_resolver,
    build_time_resolver,
    index_media,
    relocate_marker,
)
from scripts.make_samples import make_clip
from scripts.proxy_association_experiment import build_variant


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def source_clip(tmp_path):
    src = str(tmp_path / "SHOT_A.mp4")
    make_clip("SHOT_A", src, 960, 540, frame_count=24)
    return src


@pytest.fixture()
def source_pngs(tmp_path):
    from scripts.proxy_association_experiment import _render_source_frames

    png_dir = str(tmp_path / "pngs")
    os.makedirs(png_dir, exist_ok=True)
    return _render_source_frames("SHOT_A", 960, 540, 24, png_dir)


def _variant(tmp_path, source_pngs, name):
    out = str(tmp_path / name)
    os.makedirs(out, exist_ok=True)
    proxy, indices, declared = build_variant(source_pngs, name, out, 24.0)
    return proxy, indices, declared


# --------------------------------------------------------------------------
# Healthy time-preserving proxy: both schemes agree with the V1 contract
# --------------------------------------------------------------------------


def test_both_schemes_exact_on_healthy_reencode(source_clip, tmp_path):
    proxy = str(tmp_path / "proxy.mp4")
    make_proxy(source_clip, proxy, height=270)
    sm, pm = probe(source_clip), probe(proxy)

    time_resolver = build_time_resolver(sm, pm)
    content_resolver = build_content_resolver(source_clip, proxy)

    for frame in range(pm["frame_count"]):
        tr = time_resolver.resolve_proxy_frame(frame)
        cr = content_resolver.resolve_proxy_frame(frame)
        assert tr.status == "exact" and tr.source_frame == frame
        assert cr.status == "exact" and cr.source_frame == frame


def test_fingerprint_index_covers_every_frame(source_clip):
    index = index_media(source_clip, grid=16, use_cache=False)
    assert index.frame_count == 24
    assert index.fingerprints.shape == (24, 16, 16)
    best, distance, _ = index.nearest(index.frame(7))
    assert best == 7
    assert distance == pytest.approx(0.0, abs=1e-6)


# --------------------------------------------------------------------------
# Scheme A: source-time mapping
# --------------------------------------------------------------------------


def test_time_map_silently_shifts_after_head_trim(tmp_path, source_pngs):
    source = str(tmp_path / "src.mp4")
    from scripts.proxy_association_experiment import _encode_png_sequence

    _encode_png_sequence(source_pngs, source, 24)
    proxy, indices, declared = _variant(tmp_path, source_pngs, "head_trim")
    sm, pm = probe(source), probe(proxy)

    naive = TimeMappingResolver.from_metas(sm, pm, provenance="declared")
    # Every naive answer is off by exactly the 4 deleted head frames.
    residuals = [
        abs(naive.resolve_proxy_frame(p).source_frame - truth)
        for p, truth in enumerate(indices)
    ]
    assert max(residuals) == 4 and min(residuals) == 4

    # The same formula with exact provenance (offset + coverage) is exact again.
    informed = TimeMappingResolver.from_metas(
        sm, pm, start_offset=indices[0], coverage=set(indices),
        provenance="exact-log",
    )
    assert all(
        informed.resolve_proxy_frame(p).source_frame == truth
        for p, truth in enumerate(indices)
    )


def test_time_map_out_of_range_is_explicit(source_clip):
    sm = probe(source_clip)
    resolver = TimeMappingResolver.from_metas(sm, sm)
    res = resolver.resolve_proxy_frame(99)
    assert res.status == "out_of_range" and res.source_frame is None


def test_time_map_marks_deleted_coverage_gap_ambiguous(tmp_path, source_pngs):
    source = str(tmp_path / "src.mp4")
    from scripts.proxy_association_experiment import _encode_png_sequence

    _encode_png_sequence(source_pngs, source, 24)
    proxy, indices, _ = _variant(tmp_path, source_pngs, "mid_drop")
    sm, pm = probe(source), probe(proxy)
    informed = TimeMappingResolver.from_metas(
        sm, pm, start_offset=0, coverage=set(indices), provenance="exact-log"
    )
    statuses = {informed.resolve_proxy_frame(p).status for p in range(len(indices))}
    assert "ambiguous" in statuses  # the deleted middle region cannot be mapped


# --------------------------------------------------------------------------
# Scheme B: content anchors
# --------------------------------------------------------------------------


def test_content_anchor_survives_head_trim_and_mid_drop(tmp_path, source_pngs):
    source = str(tmp_path / "src.mp4")
    from scripts.proxy_association_experiment import _encode_png_sequence

    _encode_png_sequence(source_pngs, source, 24)
    for name in ("head_trim", "mid_drop"):
        proxy, indices, _ = _variant(tmp_path, source_pngs, name)
        resolver = build_content_resolver(source, proxy)
        for proxy_frame, truth in enumerate(indices):
            res = resolver.resolve_proxy_frame(proxy_frame)
            assert res.reliable, f"{name} frame {proxy_frame} -> {res.status}"
            assert res.source_frame == truth


def test_content_anchor_handles_rate_conversion(tmp_path, source_pngs):
    source = str(tmp_path / "src.mp4")
    from scripts.proxy_association_experiment import _encode_png_sequence

    _encode_png_sequence(source_pngs, source, 24)
    proxy, indices, _ = _variant(tmp_path, source_pngs, "rate_convert")
    sm = probe(source)
    assert probe(proxy)["fps"] == 30
    resolver = build_content_resolver(source, proxy)
    # Nearest-frame resampling means every proxy frame depicts a real source
    # frame; content anchoring must recover it without any fps provenance.
    for proxy_frame, truth in enumerate(indices):
        res = resolver.resolve_proxy_frame(proxy_frame)
        assert res.reliable and res.source_frame == truth


def test_frozen_run_is_ambiguous_for_marker_relocation(tmp_path, source_pngs):
    source = str(tmp_path / "src.mp4")
    from scripts.proxy_association_experiment import _encode_png_sequence

    _encode_png_sequence(source_pngs, source, 24)
    proxy, indices, _ = _variant(tmp_path, source_pngs, "frozen_run")
    resolver = build_content_resolver(source, proxy)

    # Forward association of the frozen proxy frames is still correct...
    for proxy_frame, truth in enumerate(indices):
        assert resolver.resolve_proxy_frame(proxy_frame).source_frame == truth

    # ...but relocating a marker onto the frozen source content cannot pick a
    # unique proxy position, and markers on deleted frames are refused.
    frozen = relocate_marker(resolver, 9)
    assert frozen["status"] == "ambiguous" and frozen["residual"] in (0, None)
    deleted = relocate_marker(resolver, 11)
    assert deleted["status"] == "unrelocatable"
    assert deleted["proxy_frame"] is None


def test_sparse_anchors_trade_index_size_for_residual(tmp_path, source_pngs):
    source = str(tmp_path / "src.mp4")
    from scripts.proxy_association_experiment import _encode_png_sequence

    _encode_png_sequence(source_pngs, source, 24)
    proxy, indices, _ = _variant(tmp_path, source_pngs, "mid_drop")
    full = build_content_resolver(source, proxy, anchor_step=1)
    sparse = build_content_resolver(source, proxy, anchor_step=4)

    full_info = full.describe_index()
    sparse_info = sparse.describe_index()
    assert sparse_info["source_anchors"] < full_info["source_anchors"]
    assert sparse_info["anchor_table_bytes"] < full_info["anchor_table_bytes"]

    # Sparse anchoring keeps every answer on a real frame; worst-case residual
    # is bounded by the anchor spacing, while full anchoring stays exact.
    assert all(
        full.resolve_proxy_frame(p).source_frame == truth
        for p, truth in enumerate(indices)
    )
    worst = max(
        abs(sparse.resolve_proxy_frame(p).source_frame - truth)
        for p, truth in enumerate(indices)
    )
    assert worst <= 4


# --------------------------------------------------------------------------
# Marker relocation: meaning preservation across a re-encode
# --------------------------------------------------------------------------


def test_relocate_preserves_markers_that_survive(tmp_path, source_pngs):
    source = str(tmp_path / "src.mp4")
    from scripts.proxy_association_experiment import _encode_png_sequence

    _encode_png_sequence(source_pngs, source, 24)
    proxy, indices, _ = _variant(tmp_path, source_pngs, "head_trim")
    resolver = build_content_resolver(source, proxy)
    truth_by_source = {s: p for p, s in enumerate(indices)}

    for marker in (6, 17, 23):
        rel = relocate_marker(resolver, marker)
        assert rel["status"] in ("exact", "interpolated")
        assert rel["proxy_frame"] == truth_by_source[marker]
        assert rel["residual"] == 0

    # A marker sitting on a deleted head frame cannot be relocated.
    gone = relocate_marker(resolver, 2)
    assert gone["proxy_frame"] is None
    assert gone["status"] == "unrelocatable"


def test_resolution_dataclass_carries_status_and_distance():
    res = FrameResolution(3, 7, "exact", distance=0.5)
    assert res.reliable and res.distance == 0.5
    assert not FrameResolution(3, None, "unmatched").reliable


# --------------------------------------------------------------------------
# Production frame-lookup entry point (FramePreview) is actually driven by
# both experimental schemes - not just architecture text.
# --------------------------------------------------------------------------


def test_frame_preview_uses_resolver_status(qapp, source_clip, tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from proxyworkstation.ui.preview import FramePreview

    proxy = str(tmp_path / "proxy.mp4")
    make_proxy(source_clip, proxy, height=270)
    resolver = build_content_resolver(source_clip, proxy)

    preview = FramePreview()
    assert preview.load_media(proxy, frame_map=None, resolver=resolver)
    src_frame, label = preview._source_frame(5)
    assert src_frame == 5
    assert label.startswith("content_anchor:exact")

    time_resolver = build_time_resolver(probe(source_clip), probe(proxy))
    preview.load_media(proxy, frame_map=None, resolver=time_resolver)
    src_frame, label = preview._source_frame(5)
    assert src_frame == 5 and label.startswith("source_time_map:exact")

    # V1 path remains available unchanged when no resolver is supplied.
    preview.load_media(proxy, frame_map={"source_start_offset": 0})
    src_frame, label = preview._source_frame(5)
    assert src_frame == 5 and label == "v1-frame-map:exact"


def test_main_window_exposes_both_experimental_schemes(qapp, proxied):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from proxyworkstation.ui.main_window import MainWindow

    win = MainWindow(
        os.path.join(proxied["tmp"], "project.db"), proxied["proxy_dir"]
    )
    win.ws.close()
    from proxyworkstation.core.workstation import Workstation

    win.ws = Workstation(os.path.join(proxied["tmp"], "project.db"))
    try:
        schemes = [
            win.association_combo.itemText(i)
            for i in range(win.association_combo.count())
        ]
        assert schemes == ["v1-frame-map", "source-time-map", "content-anchor"]

        win.source_table.selectRow(0)
        win.quality_combo.setCurrentText("proxy")
        for scheme in ("source-time-map", "content-anchor"):
            win.association_combo.setCurrentText(scheme)
            assert win.preview._resolver is not None
            assert win.preview._resolver.scheme in (
                "source_time_map", "content_anchor"
            )
            src_frame, label = win.preview._source_frame(0)
            assert src_frame == 0 and f":exact" in label
    finally:
        win.close()
