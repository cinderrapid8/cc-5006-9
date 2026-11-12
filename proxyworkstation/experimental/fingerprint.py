"""Per-frame content fingerprints used as content anchors.

A fingerprint is a small grayscale thumbnail (default 16x16) decoded straight
from the media through an ffmpeg rawvideo pipe, so indexing a clip needs one
decoding pass instead of one ffmpeg invocation per frame. Distance is mean
absolute difference on the 0..255 grid, the same scale the project's existing
proxy MAE verification uses.

The index is deliberately self-contained: it can be pickled to a sidecar
cache, but never touches the V1 project database.
"""

import hashlib
import os
import pickle
import subprocess

import numpy as np

from ..core.ffutils import ffmpeg_bin, probe

DEFAULT_GRID = 16


def frame_fingerprint(gray_rows, grid=DEFAULT_GRID):
    """Average a grayscale byte plane down to a ``grid x grid`` uint8 vector."""
    plane = np.frombuffer(gray_rows, dtype=np.uint8).reshape(grid, grid)
    return plane.copy()


def index_media(path, grid=DEFAULT_GRID, cache_path=None, use_cache=True):
    """Decode every frame of ``path`` and return a :class:`FingerprintIndex`.

    If ``cache_path`` is given and holds a cache whose key (path hash, size,
    frame count, grid) matches, the cached fingerprints are reused; otherwise
    the media is decoded and the cache (when requested) is written.
    """
    if use_cache and cache_path and os.path.isfile(cache_path):
        try:
            index = FingerprintIndex.load(cache_path)
            if index.cache_key == _cache_key(path, grid):
                return index
        except Exception:
            pass  # stale or corrupt cache: rebuild rather than trust it

    meta = probe(path)
    fingerprints = _decode_gray_grid(path, meta, grid)
    index = FingerprintIndex(
        path=os.path.abspath(path),
        grid=grid,
        fingerprints=fingerprints,
        frame_count=meta["frame_count"],
        width=meta["width"],
        height=meta["height"],
        cache_key=_cache_key(path, grid),
    )
    if cache_path:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)) or ".", exist_ok=True)
        index.save(cache_path)
    return index


def _cache_key(path, grid):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return f"{h.hexdigest()}:{os.path.getsize(path)}:{grid}"


def _decode_gray_grid(path, meta, grid):
    """One ffmpeg pass: scale to grid x grid, output raw gray8 frames."""
    cmd = [
        ffmpeg_bin(),
        "-v",
        "error",
        "-i",
        path,
        "-vf",
        f"scale={grid}:{grid}:flags=area,format=gray",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"fingerprint decode failed for {path}: "
            f"{proc.stderr.decode('utf-8', 'replace')[-300:]}"
        )
    frame_size = grid * grid
    usable = (len(proc.stdout) // frame_size) * frame_size
    frames = np.frombuffer(proc.stdout[:usable], dtype=np.uint8)
    if frames.size == 0:
        raise RuntimeError(f"no frames decoded from {path}")
    frames = frames.reshape(-1, grid, grid)
    if meta["frame_count"] and frames.shape[0] != meta["frame_count"]:
        # Probe counts and decoder counts can disagree on damaged streams; the
        # decoder is the source of truth for indexing, but surface the gap.
        frames = frames[: meta["frame_count"]] if frames.shape[0] > meta["frame_count"] else frames
    return [f.copy() for f in frames]


class FingerprintIndex:
    """In-memory table of one fingerprint per frame of one media file."""

    def __init__(self, path, grid, fingerprints, frame_count, width, height, cache_key):
        self.path = path
        self.grid = grid
        self.fingerprints = np.stack(fingerprints).astype(np.uint8)
        self.frame_count = int(frame_count)
        self.width = width
        self.height = height
        self.cache_key = cache_key

    def frame(self, index):
        return self.fingerprints[int(index)]

    def distances(self, fingerprint):
        """Mean absolute difference (0..255) against every indexed frame."""
        import numpy as np

        diff = np.abs(self.fingerprints.astype(np.int16) - fingerprint.astype(np.int16))
        return diff.reshape(self.fingerprints.shape[0], -1).mean(axis=1)

    def nearest(self, fingerprint):
        """Return (frame_index, distance, runner_up_distance) over all frames."""
        scores = self.distances(fingerprint)
        order = scores.argsort()
        best = int(order[0])
        second = float(scores[order[1]]) if len(order) > 1 else float("inf")
        return best, float(scores[best]), second

    def save(self, cache_path):
        with open(cache_path, "wb") as fh:
            pickle.dump(
                {
                    "path": self.path,
                    "grid": self.grid,
                    "fingerprints": self.fingerprints,
                    "frame_count": self.frame_count,
                    "width": self.width,
                    "height": self.height,
                    "cache_key": self.cache_key,
                },
                fh,
                protocol=4,
            )

    @classmethod
    def load(cls, cache_path):
        with open(cache_path, "rb") as fh:
            data = pickle.load(fh)
        return cls(
            path=data["path"],
            grid=data["grid"],
            fingerprints=list(data["fingerprints"]),
            frame_count=data["frame_count"],
            width=data["width"],
            height=data["height"],
            cache_key=data["cache_key"],
        )
