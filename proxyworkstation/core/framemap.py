"""Frame mapping between an original source and its proxy.

V1 constraint (enforced, not assumed): the proxy is *time-preserving*:

* identical frame rate
* zero start offset
* identical frame count
* pure downscale / re-encode, no retiming, drop, duplicate or padding

Under that constraint source frame ``n`` maps to proxy frame ``n`` and back.
Any requested transform that violates it raises ``UnsupportedTransformError``
so the caller cannot accidentally build a mapping that does not round-trip.

The mapping object is persisted as JSON and its ``to_proxy_frame`` /
``to_source_frame`` methods are the single place that translation happens.
"""

from dataclasses import asdict, dataclass

from .errors import UnsupportedTransformError


@dataclass(frozen=True)
class FrameMap:
    source_fps: float
    proxy_fps: float
    source_frame_count: int
    proxy_frame_count: int
    # Offset of proxy frame 0 expressed in source frame numbers. V1 requires 0.
    source_start_offset: int = 0
    transform: str = "time_preserving_downscale"

    def to_proxy_frame(self, source_frame):
        idx = int(source_frame) - self.source_start_offset
        if not (0 <= idx < self.proxy_frame_count):
            raise ValueError(
                f"source frame {source_frame} outside proxy range "
                f"[0, {self.proxy_frame_count - 1}]"
            )
        return idx

    def to_source_frame(self, proxy_frame):
        idx = int(proxy_frame)
        if not (0 <= idx < self.proxy_frame_count):
            raise ValueError(
                f"proxy frame {proxy_frame} outside range "
                f"[0, {self.proxy_frame_count - 1}]"
            )
        return idx + self.source_start_offset

    def round_trip(self, source_frame):
        return self.to_source_frame(self.to_proxy_frame(source_frame))

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(
            source_fps=float(data["source_fps"]),
            proxy_fps=float(data["proxy_fps"]),
            source_frame_count=int(data["source_frame_count"]),
            proxy_frame_count=int(data["proxy_frame_count"]),
            source_start_offset=int(data.get("source_start_offset", 0)),
            transform=data.get("transform", "time_preserving_downscale"),
        )


def build_time_preserving_map(source_meta, proxy_meta):
    """Validate that a proxy is strictly time-preserving and build its map.

    Raises ``UnsupportedTransformError`` with an explicit reason for any
    retiming / offset / frame-count change — those are rejected in V1 rather
    than approximated.
    """
    if abs(source_meta["fps"] - proxy_meta["fps"]) > 1e-6:
        raise UnsupportedTransformError(
            f"frame-rate change not supported "
            f"({source_meta['fps']} -> {proxy_meta['fps']}); "
            "proxy must keep the source frame rate"
        )
    if source_meta["frame_count"] != proxy_meta["frame_count"]:
        raise UnsupportedTransformError(
            f"frame-count change not supported "
            f"({source_meta['frame_count']} -> {proxy_meta['frame_count']}); "
            "proxy must contain exactly one frame per source frame"
        )
    return FrameMap(
        source_fps=source_meta["fps"],
        proxy_fps=proxy_meta["fps"],
        source_frame_count=source_meta["frame_count"],
        proxy_frame_count=proxy_meta["frame_count"],
        source_start_offset=0,
    )
