"""Proxy generation, parameter persistence and corruption detection."""

import os
import shutil

import pytest

from proxyworkstation.core.errors import CorruptProxyError, UnsupportedTransformError
from proxyworkstation.core.verification import verify_proxy


def test_proxy_records_full_generation_parameters(proxied):
    ws = proxied["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    record = ws.store.get_proxy_for(source["content_hash"])
    assert record["height"] == 270
    assert record["crf"] == 28
    assert record["preset"] == "veryfast"
    assert os.path.isfile(record["proxy_path"])
    report = record["verify_report"]
    assert report["passed"] is True
    assert report["fps_match"] is True
    assert report["frame_count_match"] is True
    assert report["max_mae"] < report["mae_limit"]
    assert len(report["checked_frames"]) >= 3
    for entry in report["checked_frames"]:
        assert entry["round_trip_frame"] == entry["frame"]


def test_verification_samples_include_boundaries():
    from proxyworkstation.core.verification import sample_indices

    idx = sample_indices(24, max_samples=8)
    assert idx[0] == 0
    assert idx[-1] == 23
    assert len(idx) == len(set(idx))


def test_corrupt_proxy_bytes_fail_verification(proxied):
    ws = proxied["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    record = ws.store.get_proxy_for(source["content_hash"])
    corrupt = record["proxy_path"] + ".broken.mp4"
    shutil.copy2(record["proxy_path"], corrupt)
    # Overwrite inside the actual file extent (small CRF28 proxies may be
    # only a few kilobytes), targeting the mdat region past the header.
    size = os.path.getsize(corrupt)
    with open(corrupt, "r+b") as fh:
        fh.seek(max(0, size // 2))
        fh.write(os.urandom(min(2048, size // 4)))
    with pytest.raises(CorruptProxyError):
        verify_proxy(source["current_path"], corrupt)
    os.remove(corrupt)


def test_truncated_proxy_fails_verification(proxied):
    ws = proxied["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    record = ws.store.get_proxy_for(source["content_hash"])
    truncated = record["proxy_path"] + ".trunc.mp4"
    shutil.copy2(record["proxy_path"], truncated)
    os.truncate(truncated, os.path.getsize(truncated) // 3)
    with pytest.raises(Exception):
        verify_proxy(source["current_path"], truncated)
    os.remove(truncated)


def test_proxy_built_from_other_source_fails_content_check(proxied, tmp_path):
    ws = proxied["ws"]
    source_a = ws.store.get_source_by_shot("SHOT_A")
    source_b = ws.store.get_source_by_shot("SHOT_B")
    record_b = ws.store.get_proxy_for(source_b["content_hash"])
    # SHOT_B's proxy must not verify against SHOT_A's original. Frame counts
    # differ (30 vs 24), so the structural mapping gate rejects it first.
    with pytest.raises((UnsupportedTransformError, CorruptProxyError)):
        verify_proxy(source_a["current_path"], record_b["proxy_path"])

    # Equal-length, different-content pair must fail the pixel-content gate.
    other = str(tmp_path / "other_24f.mp4")
    from scripts.make_samples import make_clip

    make_clip("SHOT_B", other, 960, 540, frame_count=24, tag="DIFFERENT-CONTENT")
    other_proxy = str(tmp_path / "other_proxy.mp4")
    from proxyworkstation.core.ffutils import make_proxy

    make_proxy(other, other_proxy, height=270)
    from proxyworkstation.core.errors import ProxyVerificationError

    with pytest.raises(ProxyVerificationError):
        verify_proxy(source_a["current_path"], other_proxy)


def test_missing_proxy_file_is_corrupt_error(workspace):
    ws = workspace["ws"]
    source = ws.store.get_source_by_shot("SHOT_A")
    with pytest.raises(CorruptProxyError):
        verify_proxy(source["current_path"], "/nonexistent/proxy.mp4")


def test_batch_reports_per_shot_failures(workspace):
    ws = workspace["ws"]
    # SHOT_A gets a valid proxy; an unknown shot id is reported, not fatal.
    results = ws.generate_proxies_batch(
        ["SHOT_A", "GHOST_SHOT"], workspace["proxy_dir"], height=270
    )
    assert [r["shot_id"] for r in results["succeeded"]] == ["SHOT_A"]
    assert results["failed"][0]["shot_id"] == "GHOST_SHOT"
