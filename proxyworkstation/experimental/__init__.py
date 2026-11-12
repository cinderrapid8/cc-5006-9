"""Experimental proxy-frame association prototypes.

Two independently testable association schemes live here, behind one small
resolver interface (``FrameResolver``) so the production frame-lookup entry
(``FramePreview``) can be driven by either one without changing the project
format:

* ``resolvers.TimeMappingResolver``  - association by source-time mapping
  (frame-rate / offset / frame-count provenance),
* ``resolvers.ContentAnchorResolver`` - association by content anchors
  (per-frame fingerprints matched against the original).

Nothing in this package writes to the V1 SQLite schema; fingerprint indexes
are cached in sidecar files only.
"""

from .fingerprint import FingerprintIndex, frame_fingerprint, index_media
from .resolvers import (
    ContentAnchorResolver,
    FrameResolution,
    TimeMappingResolver,
    build_content_resolver,
    build_time_resolver,
    relocate_marker,
)

__all__ = [
    "FingerprintIndex",
    "frame_fingerprint",
    "index_media",
    "ContentAnchorResolver",
    "FrameResolution",
    "TimeMappingResolver",
    "build_content_resolver",
    "build_time_resolver",
    "relocate_marker",
]
