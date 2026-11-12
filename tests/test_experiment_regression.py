"""The committed marker-relocation residual data must be regenerable by code
and must keep substantiating the documented decisions.

This test does not trust the JSON snapshot on its own: it re-runs the full
in-repo experiment into a temporary directory and compares the headline
residuals against both the freshly generated report and the committed one.
"""

import json
import os

from scripts.proxy_association_experiment import run_experiment

COMMITTED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "experiments",
    "proxy_association",
    "association_report.json",
)


def _headline(report):
    """Compact, decision-relevant residual matrix per variant/scheme."""
    out = {}
    for variant, vr in report["variants"].items():
        out[variant] = {}
        for scheme, sr in vr["schemes"].items():
            out[variant][scheme] = {
                "fwd_max": sr["forward"]["max_abs_residual"],
                "fwd_mean": sr["forward"]["mean_abs_residual"],
                "marker_summary": sr["markers"],
                "unreliable": sr["forward"]["unreliable_frames"],
            }
    return out


def test_experiment_regenerates_committed_residuals(tmp_path):
    fresh_report, _ = run_experiment(str(tmp_path / "exp"))
    fresh = _headline(fresh_report)

    with open(COMMITTED, encoding="utf-8") as fh:
        committed = _headline(json.load(fh))

    assert set(fresh) == set(committed)
    for variant in fresh:
        assert set(fresh[variant]) == set(committed[variant])
        for scheme in fresh[variant]:
            assert fresh[variant][scheme] == committed[variant][scheme], (
                f"residual drift in {variant}/{scheme}; regenerate "
                "experiments/proxy_association/association_report.json"
            )


def test_committed_report_encodes_the_documentated_decisions():
    with open(COMMITTED, encoding="utf-8") as fh:
        report = json.load(fh)

    # Healthy re-encode: every scheme is exactly frame-accurate.
    healthy = report["variants"]["reencode_healthy"]["schemes"]
    for scheme, sr in healthy.items():
        assert sr["forward"]["max_abs_residual"] == 0

    # Head trim: declared time mapping shifts every answer by the deleted
    # count, while full content anchoring stays exact.
    head = report["variants"]["head_trim"]["schemes"]
    assert head["time_map_declared"]["forward"]["max_abs_residual"] == 4
    assert head["content_anchor_step1"]["forward"]["max_abs_residual"] == 0

    # Mid drop: even an exact transform log cannot map the deleted gap; the
    # content resolver reports zero residual on every surviving frame.
    drop = report["variants"]["mid_drop"]["schemes"]
    assert drop["time_map_informed"]["forward"]["unreliable_frames"]
    assert drop["content_anchor_step1"]["forward"]["max_abs_residual"] == 0

    # Frozen run: content anchoring flags the repeated content instead of
    # guessing a unique position for the marker.
    frozen = report["variants"]["frozen_run"]["schemes"]["content_anchor_step1"]
    assert any(
        m["status"] == "ambiguous" for m in frozen["marker_rows"]
    )
    assert any(
        m["status"] == "unrelocatable" for m in frozen["marker_rows"]
    )

    # Index cost: content anchoring decodes both media; time mapping decodes
    # nothing and stores no index.
    drop_anchor = drop["content_anchor_step1"]["index"]
    assert drop_anchor["index_frames_decoded"] > 0
    assert drop["time_map_declared"]["index"]["index_frames_decoded"] == 0
    assert drop["time_map_declared"]["index"]["index_bytes"] == 0
