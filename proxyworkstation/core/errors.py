"""Domain exceptions.

An unsupported proxy transform is rejected explicitly instead of being
silently approximated, because the workstation must only accept proxies whose
frames can be mapped back to exact original frames.
"""


class WorkstationError(Exception):
    """Base class for all workstation errors."""


class UnsupportedTransformError(WorkstationError):
    """Raised when a requested proxy transform breaks frame-accurate mapping.

    V1 only supports time-preserving proxies: same frame rate, zero start
    offset, same frame count (pure downscale / re-encode).
    """


class ProxyVerificationError(WorkstationError):
    """Raised when a generated proxy fails the frame-mapping verification."""


class MediaReadError(WorkstationError):
    """Raised when a media file cannot be probed or decoded."""


class MissingSourceError(WorkstationError):
    """Raised at render time when an original source required by the cut is offline.

    The renderer must never substitute a low-resolution proxy for the missing
    original; the render is blocked and every missing item is listed.
    """

    def __init__(self, missing):
        self.missing = list(missing)
        names = ", ".join(sorted(m.get("shot_id", "?") for m in self.missing))
        super().__init__(
            f"{len(self.missing)} original source(s) offline, render blocked: {names}"
        )


class CorruptProxyError(WorkstationError):
    """Raised when a proxy file is unreadable or fails integrity checks."""
