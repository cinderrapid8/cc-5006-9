"""Application service: the single API the UI and CLI drive.

Key guarantees implemented here:

* import keys media by content hash, so same-named different content stays
  distinct and re-importing a moved file links to the same logical shot;
* proxy generation records parameters and the verified FrameMap, and rejects
  unsupported transforms explicitly;
* clips store source-frame intervals, independent of preview resolution;
* offline originals never block proxy editing, but final rendering blocks and
  lists every missing source instead of substituting a proxy.
"""

import os

from .errors import (
    CorruptProxyError,
    MissingSourceError,
    UnsupportedTransformError,
    WorkstationError,
)
from .ffutils import make_proxy, probe, render_concat
from .framemap import build_time_preserving_map
from .identity import identity_for, verify_hash
from .store import ProjectStore
from .verification import verify_proxy


class Workstation:
    def __init__(self, db_path):
        self.store = ProjectStore(db_path)

    def close(self):
        self.store.close()

    # -- import ------------------------------------------------------------

    def import_source(self, path, shot_id):
        """Register an original as a logical shot. Content-hash based."""
        meta = probe(path)
        identity = identity_for(path)
        existing = self.store.get_source_by_hash(identity["content_hash"])
        self.store.add_source(identity, shot_id, meta, path)
        return {
            "shot_id": shot_id,
            "content_hash": identity["content_hash"],
            "already_known": existing is not None,
            "meta": meta,
        }

    # -- proxy generation --------------------------------------------------

    def generate_proxy(self, shot_id, proxy_dir, height=360, crf=28, preset="veryfast"):
        """Full automatic proxy task: build, verify, persist.

        Raises UnsupportedTransformError if the produced proxy would not map
        back exactly (e.g. a caller-supplied retime), CorruptProxyError if the
        output is broken.
        """
        source = self.store.get_source_by_shot(shot_id)
        if source is None:
            raise WorkstationError(f"unknown logical shot: {shot_id}")
        src_path = source["current_path"]
        if not os.path.isfile(src_path):
            raise WorkstationError(
                f"original for {shot_id} is offline; cannot generate proxy "
                f"(looked in {src_path})"
            )

        if height >= source["height"]:
            raise UnsupportedTransformError(
                f"proxy height {height} is not lower than source height "
                f"{source['height']}; a proxy must downscale"
            )

        os.makedirs(proxy_dir, exist_ok=True)
        proxy_path = os.path.join(
            os.path.abspath(proxy_dir), f"{shot_id}_proxy_h{height}.mp4"
        )
        make_proxy(src_path, proxy_path, height=height, crf=crf, preset=preset)

        # Probe both and enforce the V1 time-preserving contract before trust.
        source_meta = probe(src_path)
        proxy_meta = probe(proxy_path)
        frame_map = build_time_preserving_map(source_meta, proxy_meta)

        # Content verification: sampled frames must match and round-trip.
        report = verify_proxy(src_path, proxy_path)

        proxy_identity = identity_for(proxy_path)
        params = {
            "height": height,
            "crf": crf,
            "preset": preset,
            "fps": proxy_meta["fps"],
            "frame_count": proxy_meta["frame_count"],
            "width": proxy_meta["width"],
        }
        self.store.add_proxy(
            proxy_identity,
            source["content_hash"],
            proxy_path,
            params,
            frame_map,
            report,
        )
        return {
            "shot_id": shot_id,
            "proxy_path": proxy_path,
            "params": params,
            "frame_map": frame_map.to_dict(),
            "report": report,
        }

    def generate_proxies_batch(self, shot_ids, proxy_dir, **kwargs):
        """Automatic queue: per-shot results, never failing the whole batch silently."""
        results = {"succeeded": [], "failed": []}
        for shot_id in shot_ids:
            try:
                results["succeeded"].append(
                    self.generate_proxy(shot_id, proxy_dir, **kwargs)
                )
            except WorkstationError as exc:
                results["failed"].append({"shot_id": shot_id, "error": str(exc)})
        return results

    # -- preview selection -------------------------------------------------

    def resolve_media(self, content_hash, quality):
        """Return media path for a quality.

        ``proxy`` falls back to the original only if no proxy exists (editing
        stays possible). ``original`` NEVER falls back to a proxy: if the
        original is offline the status says so and rendering is blocked later.
        """
        source = self.store.get_source_by_hash(content_hash)
        proxy = self.store.get_proxy_for(content_hash)
        source_online = (
            os.path.isfile(source["current_path"])
            and verify_hash(source["current_path"], content_hash)[0]
        )
        proxy_ok = bool(proxy) and os.path.isfile(proxy["proxy_path"])

        if quality == "proxy":
            if proxy_ok:
                return {
                    "path": proxy["proxy_path"],
                    "kind": "proxy",
                    "map": proxy["frame_map"],
                    "online": True,
                }
            return {
                "path": source["current_path"],
                "kind": "original-fallback",
                "map": None,
                "online": source_online,
            }

        return {
            "path": source["current_path"],
            "kind": "original",
            "map": None,
            "online": source_online,
            "proxy_available": proxy_ok,
        }

    #-- relocation --------------------------------------------------------

    def relocate_source(self, content_hash, new_path):
        """Re-link a moved original. The content hash must match exactly.

        A same-named but different-content file is rejected — identity is not
        derived from the filename.
        """
        source = self.store.get_source_by_hash(content_hash)
        if source is None:
            raise WorkstationError("unknown source hash")
        ok, actual_hash = verify_hash(new_path, content_hash)
        if not ok:
            raise WorkstationError(
                f"relocation refused: content mismatch at {new_path} "
                f"(expected {content_hash[:12]}, got {actual_hash[:12]}). "
                "A same-named file with different bytes cannot replace the original."
            )
        self.store.update_source_path(content_hash, new_path)
        return self.store.get_source_by_hash(content_hash)

    def relocate_proxy(self, proxy_hash, new_path):
        if not os.path.isfile(new_path):
            raise CorruptProxyError(f"proxy not found: {new_path}")
        ok, actual = verify_hash(new_path, proxy_hash)
        if not ok:
            raise CorruptProxyError(
                f"proxy relocation refused: hash mismatch (got {actual[:12]})"
            )
        self.store.update_proxy_path(proxy_hash, new_path)
        return True

    def auto_relocate(self, content_hash, candidate_paths):
        """Search candidate files (or folders, walked recursively) for a hash match."""
        candidates = []
        for path in candidate_paths:
            if os.path.isdir(path):
                for dirpath, _, filenames in os.walk(path):
                    candidates.extend(
                        os.path.join(dirpath, name) for name in filenames
                    )
            elif os.path.isfile(path):
                candidates.append(path)
        for path in candidates:
            ok, _ = verify_hash(path, content_hash)
            if ok:
                return self.relocate_source(content_hash, path)
        return None

    #-- timeline ----------------------------------------------------------

    def add_clip(self, shot_id, source_in, source_out):
        source = self.store.get_source_by_shot(shot_id)
        if source is None:
            raise WorkstationError(f"unknown logical shot: {shot_id}")
        if source_in < 0 or source_out > source["frame_count"]:
            raise ValueError(
                f"interval [{source_in}, {source_out}) outside source "
                f"[0, {source['frame_count']})"
            )
        clip_id = self.store.add_clip(source["content_hash"], source_in, source_out)
        return clip_id

    #-- render ------------------------------------------------------------

    def preflight(self):
        """Check every source used by the timeline before final render."""
        clips = self.store.list_clips()
        missing = []
        for clip in clips:
            source = self.store.get_source_by_hash(clip["content_hash"])
            path = source["current_path"]
            if not os.path.isfile(path):
                missing.append(
                    {
                        "shot_id": source["shot_id"],
                        "reason": "file missing",
                        "path": path,
                        "clip_id": clip["clip_id"],
                    }
                )
            else:
                ok, _ = verify_hash(path, source["content_hash"])
                if not ok:
                    missing.append(
                        {
                            "shot_id": source["shot_id"],
                            "reason": "content hash mismatch (file replaced)",
                            "path": path,
                            "clip_id": clip["clip_id"],
                        }
                    )
        return {
            "ready": not missing,
            "missing": missing,
            "clip_count": len(clips),
        }

    def render_final(self, out_path, width=None, height=None, fps=None, crf=18,
                     preset="medium"):
        """Render the final program from originals only.

        Blocked if any original is offline or altered — proxies are never used
        as final-picture substitutes.
        """
        check = self.preflight()
        if not check["ready"]:
            raise MissingSourceError(check["missing"])

        clips = self.store.list_clips()
        segments = []
        for clip in clips:
            source = self.store.get_source_by_hash(clip["content_hash"])
            segments.append(
                (source["current_path"], clip["source_in"], clip["source_out"])
            )
            # Output settings default to the first source's native parameters.
        first = self.store.get_source_by_hash(clips[0]["content_hash"])
        width = width or first["width"]
        height = height or first["height"]
        fps = fps or first["fps"]

        render_concat(segments, out_path, width, height, fps, crf=crf, preset=preset)
        meta = probe(out_path)
        expected_frames = sum(c["source_out"] - c["source_in"] for c in clips)
        return {
            "out_path": os.path.abspath(out_path),
            "width": width,
            "height": height,
            "fps": fps,
            "crf": crf,
            "preset": preset,
            "expected_frames": expected_frames,
            "actual_frame_count": meta["frame_count"],
            "frame_accurate": meta["frame_count"] == expected_frames,
            "segments": len(segments),
        }
