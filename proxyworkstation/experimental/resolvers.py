"""Two proxy-frame association schemes behind one resolver interface.

Scheme A - :class:`TimeMappingResolver`
    Association by *source-time mapping*. The relationship is derived from
    transform provenance (frame rate, frame counts, start offset) and applied
    as an arithmetic formula. It costs nothing at query time, but is only as
    trustworthy as the provenance: a deleted head frame or a timecode shift
    that is not reflected in the parameters silently shifts every answer.

Scheme B - :class:`ContentAnchorResolver`
    Association by *content anchors*. Proxy frames are fingerprinted and
    matched against fingerprints of the original, optionally restricted to a
    sparse anchor set. It is robust to dropped frames and timecode changes but
    pays an indexing cost (one full decode per side, one distance table per
    query) and can only return an *unambiguous* answer where content actually
    distinguishes frames - frozen/duplicate runs are reported as ambiguous
    instead of guessed.

Both resolvers return :class:`FrameResolution` so the frame-lookup entry point
treats them uniformly, including explicit ``status`` for regions that cannot
be reliably associated.
"""

import hashlib
import os
from dataclasses import dataclass, field

import numpy as np

from .fingerprint import FingerprintIndex, index_media

# A proxy fingerprint whose two nearest source frames are closer than this
# ratio is considered non-unique (duplicate / frozen content).
UNIQUENESS_RATIO = 1.15
# Distance (0..255 MAE) above which even the best match is treated as no match.
CONTENT_DISTANCE_LIMIT = 18.0
# Re-encode matches on real corresponding content score well below this;
# anything above it is a nearest-survivor approximation, not a confident hit.
CONFIDENT_DISTANCE = 3.0


@dataclass
class FrameResolution:
    """Result of associating one proxy frame with the original."""

    proxy_frame: int
    source_frame: int | None
    status: str  # exact | interpolated | ambiguous | weak_match | out_of_range | unmatched
    detail: str = ""
    distance: float | None = None
    alternatives: tuple = field(default_factory=tuple)

    @property
    def reliable(self):
        return self.status in ("exact", "interpolated")


class FrameResolver:
    scheme = "base"

    def resolve_proxy_frame(self, proxy_frame):
        raise NotImplementedError

    def describe_index(self):
        raise NotImplementedError


# --------------------------------------------------------------------------
# Scheme A: source-time mapping
# --------------------------------------------------------------------------


class TimeMappingResolver(FrameResolver):
    """Arithmetic mapping from transform provenance.

    ``source_frame = round(proxy_frame * src_fps / proxy_fps) + start_offset``
    The map is invertible and free at query time; ``coverage`` records which
    source frames the proxy actually spans, so callers can see when the formula
    points outside covered content rather than discovering it silently.
    """

    scheme = "source_time_map"

    def __init__(self, source_fps, proxy_fps, source_frame_count,
                 proxy_frame_count, start_offset=0, coverage=None,
                 provenance="declared"):
        self.source_fps = float(source_fps)
        self.proxy_fps = float(proxy_fps)
        self.source_frame_count = int(source_frame_count)
        self.proxy_frame_count = int(proxy_frame_count)
        self.start_offset = int(start_offset)
        # Source frames actually present in the proxy. None means "assumed all".
        self.coverage = coverage
        self.provenance = provenance

    @classmethod
    def from_metas(cls, source_meta, proxy_meta, start_offset=0,
                   coverage=None, provenance="declared"):
        return cls(
            source_fps=source_meta["fps"],
            proxy_fps=proxy_meta["fps"],
            source_frame_count=source_meta["frame_count"],
            proxy_frame_count=proxy_meta["frame_count"],
            start_offset=start_offset,
            coverage=coverage,
            provenance=provenance,
        )

    def resolve_proxy_frame(self, proxy_frame):
        proxy_frame = int(proxy_frame)
        if not (0 <= proxy_frame < self.proxy_frame_count):
            return FrameResolution(
                proxy_frame, None, "out_of_range",
                f"proxy frame {proxy_frame} outside [0,{self.proxy_frame_count - 1}]",
            )
        source_frame = (
            int(round(proxy_frame * self.source_fps / self.proxy_fps))
            + self.start_offset
        )
        if not (0 <= source_frame < self.source_frame_count):
            return FrameResolution(
                proxy_frame, None, "out_of_range",
                f"mapped source frame {source_frame} outside source "
                f"[0,{self.source_frame_count - 1}]",
            )
        if self.coverage is not None and source_frame not in self.coverage:
            # Fractional-rate mappings legitimately land between integer
            # source frames; accept anything within half a frame of a covered
            # frame, otherwise the formula points at deleted/uncovered content.
            nearest = min(self.coverage, key=lambda s: abs(s - source_frame))
            if abs(nearest - source_frame) > 0.5:
                return FrameResolution(
                    proxy_frame, nearest, "ambiguous",
                    f"formula points at source {source_frame}, which is not covered; "
                    f"nearest covered frame is {nearest}",
                )
            source_frame = nearest
        return FrameResolution(
            proxy_frame, source_frame, "exact",
            f"time map ({self.provenance})",
        )

    def describe_index(self):
        return {
            "scheme": self.scheme,
            "provenance": self.provenance,
            "query_cost": "O(1) arithmetic; no media decode",
            "index_bytes": 0,
            "index_frames_decoded": 0,
            "source_fps": self.source_fps,
            "proxy_fps": self.proxy_fps,
            "start_offset": self.start_offset,
        }


def build_time_resolver(source_meta, proxy_meta, start_offset=0,
                        coverage=None, provenance="declared"):
    """Convenience factory matching the experiment harness signatures."""
    return TimeMappingResolver.from_metas(
        source_meta, proxy_meta, start_offset=start_offset,
        coverage=coverage, provenance=provenance,
    )


# --------------------------------------------------------------------------
# Scheme B: content anchors
# --------------------------------------------------------------------------


class ContentAnchorResolver(FrameResolver):
    """Match proxy frames to source frames by fingerprint content.

    ``anchor_step=1`` fingerprints every source frame (full index); larger
    steps keep only sparse anchors and linearly interpolate between them,
    which is how the index cost / precision trade-off is measured.
    """

    scheme = "content_anchor"

    def __init__(self, source_index, proxy_index, anchor_step=1,
                 distance_limit=CONTENT_DISTANCE_LIMIT,
                 confident_distance=CONFIDENT_DISTANCE,
                 uniqueness_ratio=UNIQUENESS_RATIO):
        self.source_index: FingerprintIndex = source_index
        self.proxy_index: FingerprintIndex = proxy_index
        self.anchor_step = max(1, int(anchor_step))
        self.distance_limit = float(distance_limit)
        self.confident_distance = float(confident_distance)
        self.uniqueness_ratio = float(uniqueness_ratio)
        self.anchor_frames = list(range(0, source_index.frame_count, self.anchor_step))
        if self.anchor_frames[-1] != source_index.frame_count - 1:
            self.anchor_frames.append(source_index.frame_count - 1)
        self._anchor_matrix = source_index.fingerprints[self.anchor_frames]
        self._match_cache = {}
        self._sparse_assignment = None

    def _build_sparse_assignment(self):
        """Two-stage sparse-anchor association.

        Stage 1: find proxy frames whose content matches an anchor
        confidently (within ``confident_distance``); project those onto a
        non-decreasing path so a re-encode cannot map frames backwards.
        Stage 2: linearly interpolate the source position between adjacent
        matched anchors for the proxy frames in between. Frames before the
        first / after the last confident anchor are extrapolated and flagged
        so callers can see the reduced trust rather than being given a silent
        guess.
        """
        if self._sparse_assignment is not None:
            return self._sparse_assignment
        anchor_count = len(self.anchor_frames)
        confident = []  # (proxy_frame, anchor_pos, distance)
        for proxy_frame in range(self.proxy_index.frame_count):
            fp = self.proxy_index.frame(proxy_frame).astype(np.int16)
            diff = np.abs(self._anchor_matrix.astype(np.int16) - fp)
            scores = diff.reshape(anchor_count, -1).mean(axis=1)
            pos = int(scores.argmin())
            if float(scores[pos]) <= self.confident_distance:
                confident.append((proxy_frame, pos, float(scores[pos])))

        # Monotone projection: keep the longest non-decreasing run of anchor
        # positions, dropping regressions caused by quantization noise.
        monotone = []
        last_pos = -1
        for proxy_frame, pos, dist in confident:
            if pos >= last_pos:
                monotone.append((proxy_frame, pos, dist))
                last_pos = pos
            elif monotone:
                # A regression: replace the previous point only if this one is
                # a clearly better (closer) fit at the same position.
                prev_p, prev_pos, prev_d = monotone[-1]
                if pos == prev_pos and dist < prev_d:
                    monotone[-1] = (proxy_frame, pos, dist)

        assignment = [None] * self.proxy_index.frame_count
        if not monotone:
            self._sparse_assignment = assignment
            return assignment

        first_p, first_pos, _ = monotone[0]
        last_p, last_pos, _ = monotone[-1]
        for proxy_frame, pos, dist in monotone:
            assignment[proxy_frame] = (
                self.anchor_frames[pos], "anchor", dist
            )

        # Extrapolate the head/tail against the nearest boundary anchor.
        for p in range(0, first_p):
            assignment[p] = (
                self.anchor_frames[first_pos], "extrapolated", None
            )
        for p in range(last_p + 1, self.proxy_index.frame_count):
            assignment[p] = (
                self.anchor_frames[last_pos], "extrapolated", None
            )

        # Interpolate gaps between consecutive confident anchor matches.
        for (p0, a0, _), (p1, a1, _) in zip(monotone, monotone[1:]):
            src0, src1 = self.anchor_frames[a0], self.anchor_frames[a1]
            for p in range(p0 + 1, p1):
                ratio = (p - p0) / (p1 - p0)
                interp = int(round(src0 + ratio * (src1 - src0)))
                assignment[p] = (interp, "interpolated", None)

        self._sparse_assignment = assignment
        return assignment

    def _match_anchor(self, proxy_frame):
        if proxy_frame in self._match_cache:
            return self._match_cache[proxy_frame]
        if self.anchor_step > 1:
            assignment = self._build_sparse_assignment()
            entry = assignment[proxy_frame]
            if entry is None:
                result = (None, float("inf"), None, float("inf"))
            else:
                source_frame, kind, distance = entry
                result = (
                    source_frame,
                    distance if distance is not None else float("nan"),
                    None,
                    float("inf"),
                )
            self._match_cache[proxy_frame] = result
            return result

        fp = self.proxy_index.frame(proxy_frame).astype(np.int16)
        diff = np.abs(self._anchor_matrix.astype(np.int16) - fp)
        scores = diff.reshape(self._anchor_matrix.shape[0], -1).mean(axis=1)
        order = scores.argsort()
        best_anchor_pos = int(order[0])
        best_source = self.anchor_frames[best_anchor_pos]
        best_distance = float(scores[best_anchor_pos])
        second_source = self.anchor_frames[int(order[1])] if len(order) > 1 else None
        second_distance = (
            float(scores[order[1]]) if len(order) > 1 else float("inf")
        )
        result = (best_source, best_distance, second_source, second_distance)
        self._match_cache[proxy_frame] = result
        return result

    def resolve_proxy_frame(self, proxy_frame):
        proxy_frame = int(proxy_frame)
        if not (0 <= proxy_frame < self.proxy_index.frame_count):
            return FrameResolution(
                proxy_frame, None, "out_of_range",
                f"proxy frame {proxy_frame} outside proxy range",
            )
        best_source, distance, second_source, second = self._match_anchor(proxy_frame)

        if self.anchor_step > 1:
            return self._resolve_sparse(proxy_frame, best_source, distance)

        if distance > self.distance_limit:
            return FrameResolution(
                proxy_frame, None, "unmatched",
                f"best content distance {distance:.2f} > limit {self.distance_limit}",
                distance=distance,
            )

        # Full index: the second-nearest frame is meaningful, so a near
        # tie genuinely means non-unique (frozen / duplicated) content.
        if second <= distance * self.uniqueness_ratio:
            return FrameResolution(
                proxy_frame, best_source, "ambiguous",
                f"non-unique content (distance {distance:.2f} vs "
                f"{second:.2f}); frozen or duplicated frames",
                distance=distance,
                alternatives=((second_source,)
                              if second_source is not None else ()),
            )
        if distance > self.confident_distance:
            return FrameResolution(
                proxy_frame, best_source, "weak_match",
                f"nearest-survivor match d={distance:.2f} above confident "
                f"threshold {self.confident_distance}",
                distance=distance,
            )
        return FrameResolution(
            proxy_frame, best_source, "exact",
            "unique full-index content match",
            distance=distance,
        )

    def _resolve_sparse(self, proxy_frame, best_source, distance):
        assignment = self._build_sparse_assignment()
        entry = assignment[proxy_frame]
        if entry is None:
            return FrameResolution(
                proxy_frame, None, "unmatched",
                "no confident anchor match and no bracketing anchors to "
                "interpolate between",
            )
        source_frame, kind, anchor_distance = entry
        if kind == "anchor":
            return FrameResolution(
                proxy_frame, source_frame, "exact",
                f"sparse anchor hit (step {self.anchor_step}) "
                f"d={anchor_distance:.2f}",
                distance=anchor_distance,
            )
        if kind == "extrapolated":
            return FrameResolution(
                proxy_frame, source_frame, "weak_match",
                f"extrapolated beyond outermost confident anchor "
                f"(step {self.anchor_step}); position bounded by anchor spacing",
            )
        return FrameResolution(
            proxy_frame, source_frame, "interpolated",
            f"linearly interpolated between confident anchors "
            f"(step {self.anchor_step}); worst-case error "
            f"~{self.anchor_step / 2:.1f} frames",
        )

    def describe_index(self):
        return {
            "scheme": self.scheme,
            "anchor_step": self.anchor_step,
            "source_anchors": len(self.anchor_frames),
            "source_frames_total": self.source_index.frame_count,
            "proxy_frames_indexed": self.proxy_index.frame_count,
            "index_frames_decoded": (
                self.source_index.frame_count + self.proxy_index.frame_count
            ),
            "index_bytes": int(
                self.source_index.fingerprints.nbytes
                + self.proxy_index.fingerprints.nbytes
            ),
            "anchor_table_bytes": int(self._anchor_matrix.nbytes),
            "query_cost": (
                f"O({len(self.anchor_frames)}) fingerprint distances per proxy frame"
            ),
        }


# --------------------------------------------------------------------------
# Marker relocation across a re-encode
# --------------------------------------------------------------------------


def relocate_marker(resolver, source_marker_frame, proxy_frame_count=None):
    """Relocate an edit marker (originally a source-frame number) onto the
    proxy coordinate system understood by ``resolver``.

    Returns a dict with the chosen proxy frame, residual against the nearest
    content match, and an explicit reason when no reliable target exists
    (deleted frames, frozen runs, coverage gaps). This is the operation that
    keeps clip in/out points meaningful after a proxy re-encode.
    """
    if isinstance(resolver, ContentAnchorResolver):
        return _relocate_by_content(resolver, source_marker_frame)
    return _relocate_by_time(resolver, source_marker_frame)


def _relocate_by_time(resolver, source_marker_frame):
    source_marker_frame = int(source_marker_frame)
    if not (0 <= source_marker_frame < resolver.source_frame_count):
        return {"proxy_frame": None, "status": "out_of_range",
                "reason": "marker outside source", "residual": None}
    if resolver.coverage is not None and source_marker_frame not in resolver.coverage:
        nearest = min(resolver.coverage, key=lambda s: abs(s - source_marker_frame))
        return {
            "proxy_frame": None,
            "status": "unrelocatable",
            "reason": f"source frame {source_marker_frame} was deleted from the proxy; "
                      f"nearest surviving source frame is {nearest}",
            "nearest_source_frame": nearest,
            "residual": abs(nearest - source_marker_frame),
        }
    # Invert the time formula.
    proxy_frame = int(round(
        (source_marker_frame - resolver.start_offset)
        * resolver.proxy_fps / resolver.source_fps
    ))
    proxy_frame = max(0, min(proxy_frame, resolver.proxy_frame_count - 1))
    back = resolver.resolve_proxy_frame(proxy_frame)
    residual = (
        abs(back.source_frame - source_marker_frame)
        if back.source_frame is not None else None
    )
    return {
        "proxy_frame": proxy_frame,
        "status": back.status,
        "reason": back.detail,
        "residual": residual,
    }


def _relocate_by_content(resolver, source_marker_frame):
    source_marker_frame = int(source_marker_frame)
    if not (0 <= source_marker_frame < resolver.source_index.frame_count):
        return {"proxy_frame": None, "status": "out_of_range",
                "reason": "marker outside source", "residual": None}

    target_fp = resolver.source_index.frame(source_marker_frame)
    # Search every proxy frame for the source marker's content.
    scores = []
    for p in range(resolver.proxy_index.frame_count):
        diff = np.abs(
            resolver.proxy_index.frame(p).astype(np.int16) - target_fp.astype(np.int16)
        )
        scores.append(float(diff.mean()))
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    best_proxy = order[0]
    best_distance = scores[best_proxy]
    second_distance = scores[order[1]] if len(order) > 1 else float("inf")

    if best_distance > resolver.distance_limit:
        return {
            "proxy_frame": None,
            "status": "unrelocatable",
            "reason": f"marker content absent from proxy (d={best_distance:.2f}); "
                      "frame was deleted or altered beyond matching",
            "residual": None,
        }
    if second_distance <= best_distance * resolver.uniqueness_ratio:
        return {
            "proxy_frame": best_proxy,
            "status": "ambiguous",
            "reason": f"marker content repeats in proxy (d={best_distance:.2f} vs "
                      f"{second_distance:.2f}); frozen run, position not unique",
            "residual": None,
            "alternatives": [order[1]],
        }
    if best_distance > resolver.confident_distance:
        return {
            "proxy_frame": best_proxy,
            "status": "weak_match",
            "reason": f"only a nearest-survivor frame is close (d={best_distance:.2f} "
                      f"> {resolver.confident_distance}); the marked frame itself was "
                      "deleted, so no proxy position carries its meaning",
            "residual": None,
            "distance": best_distance,
        }
    # Round-trip: which source frame does that proxy frame resolve back to?
    back = resolver.resolve_proxy_frame(best_proxy)
    residual = (
        abs(back.source_frame - source_marker_frame)
        if back.source_frame is not None else None
    )
    return {
        "proxy_frame": best_proxy,
        "status": "exact" if residual == 0 else "interpolated",
        "reason": f"content match d={best_distance:.2f}; round-trip -> "
                  f"source {back.source_frame}",
        "residual": residual,
        "distance": best_distance,
    }


def build_content_resolver(source_path, proxy_path, grid=16, anchor_step=1,
                           cache_dir=None):
    """Factory used by the UI wiring and the experiment harness."""
    def _cache(media_path):
        if not cache_dir:
            return None
        digest = hashlib.sha256(
            os.path.abspath(media_path).encode("utf-8")
        ).hexdigest()[:16]
        return os.path.join(cache_dir, f"{digest}_g{grid}.pkl")

    source_index = index_media(source_path, grid=grid, cache_path=_cache(source_path))
    proxy_index = index_media(proxy_path, grid=grid, cache_path=_cache(proxy_path))
    return ContentAnchorResolver(source_index, proxy_index, anchor_step=anchor_step)
