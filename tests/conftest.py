"""Shared pytest fixtures: isolated workspaces with synthetic media."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxyworkstation.core.workstation import Workstation  # noqa: E402
from scripts.make_samples import make_clip  # noqa: E402


@pytest.fixture()
def workspace(tmp_path):
    media = tmp_path / "media"
    proxies = tmp_path / "proxies"
    moved = tmp_path / "moved"
    db = tmp_path / "project.db"

    paths = {
        "SHOT_A": str(media / "SHOT_A.mp4"),
        "SHOT_B": str(media / "SHOT_B.mp4"),
        "SHOT_C": str(media / "SHOT_C.mp4"),
    }
    make_clip("SHOT_A", paths["SHOT_A"], 960, 540, frame_count=24)
    make_clip("SHOT_B", paths["SHOT_B"], 1280, 720, frame_count=30)
    make_clip("SHOT_C", paths["SHOT_C"], 960, 540, frame_count=18)

    ws = Workstation(str(db))
    for shot, path in paths.items():
        ws.import_source(path, shot)

    yield {
        "ws": ws,
        "media_dir": str(media),
        "proxy_dir": str(proxies),
        "moved_dir": str(moved),
        "paths": paths,
        "tmp": str(tmp_path),
    }
    ws.close()


@pytest.fixture()
def proxied(workspace):
    workspace["ws"].generate_proxies_batch(
        ["SHOT_A", "SHOT_B", "SHOT_C"], workspace["proxy_dir"], height=270
    )
    return workspace
