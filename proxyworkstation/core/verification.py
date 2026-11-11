"""Proxy verification.

A proxy is only trusted after two checks:

1. Structural: probe metadata must satisfy the time-preserving FrameMap rules
   (same fps / frame count), which itself rejects retiming and offsets.
2. Content: for a set of sampled frame numbers, the source frame and the
   proxy frame at the *same* frame number are decoded and compared after
   normalizing both to the proxy resolution. A perceptual MAE threshold
   tolerates H.264 quantization while catching corruption, swapped content
   and wrong-frame mapping. A corrupt/undecodable frame fails the check.

The check proves frame N of the proxy corresponds to frame N of the original,
i.e. the recorded mapping genuinely returns to the exact original frame.
"""

import os
import tempfile

from PIL import Image, ImageChops, ImageStat

from .errors import CorruptProxyError, ProxyVerificationError
from .ffutils import extract_frame, full_decode_check, probe
from .framemap import build_time_preserving_map

# Mean absolute pixel difference (0-255 scale) tolerated for a downscaled,
# H.264 re-encoded frame. Healthy proxies score well under 10; corruption or
# mismatched content scores far above this.
DEFAULT_MAE_LIMIT = 12.0


def _load_gray(path, size):
    img = Image.open(path)
    img.load()
    return img.convert("RGB").resize(size, Image.BILINEAR)


def _frame_mae(image_a, image_b):
    diff = ImageChops.difference(image_a, image_b)
    return sum(ImageStat.Stat(diff).mean) / 3.0


def sample_indices(frame_count, max_samples=8):
    if frame_count <= 0:
        return []
    if frame_count <= max_samples:
        return list(range(frame_count))
    # Always include first and last; evenly spaced points in between.
    step = frame_count / max_samples
    picks = [round(i * step) for i in range(max_samples)]
    picks[-1] = frame_count - 1
    return sorted(set(picks))


def verify_proxy(source_path, proxy_path, max_samples=8, mae_limit=DEFAULT_MAE_LIMIT):
    """Verify a proxy against its source. Returns a report dict or raises."""
    if not os.path.isfile(proxy_path) or os.path.getsize(proxy_path) == 0:
        raise CorruptProxyError(f"proxy missing or empty: {proxy_path}")

    try:
        source_meta = probe(source_path)
        proxy_meta = probe(proxy_path)
    except Exception as exc:
        raise CorruptProxyError(f"proxy cannot be probed: {exc}") from exc

    # Gate 0: whole-file decode must be clean, otherwise container/NAL damage
    # could be concealed during the sampled-frame comparison below.
    try:
        full_decode_check(proxy_path)
    except Exception as exc:
        raise CorruptProxyError(f"proxy fails full-decode integrity check: {exc}") from exc

    frame_map = build_time_preserving_map(source_meta, proxy_meta)
    size = (proxy_meta["width"], proxy_meta["height"])
    indices = sample_indices(frame_map.proxy_frame_count, max_samples)

    report = {
        "checked_frames": [],
        "mae_limit": mae_limit,
        "max_mae": 0.0,
        "frame_count_match": source_meta["frame_count"] == proxy_meta["frame_count"],
        "fps_match": abs(source_meta["fps"] - proxy_meta["fps"]) < 1e-6,
        "proxy_resolution": f"{proxy_meta['width']}x{proxy_meta['height']}",
        "source_resolution": f"{source_meta['width']}x{source_meta['height']}",
    }

    with tempfile.TemporaryDirectory(prefix="proxyws_verify_") as tmp:
        for idx in indices:
            src_png = os.path.join(tmp, f"src_{idx:06d}.png")
            prx_png = os.path.join(tmp, f"prx_{idx:06d}.png")
            try:
                extract_frame(source_path, idx, src_png, source_meta["fps"])
                extract_frame(proxy_path, idx, prx_png, proxy_meta["fps"])
                mae = _frame_mae(_load_gray(src_png, size), _load_gray(prx_png, size))
            except Exception as exc:
                raise CorruptProxyError(
                    f"frame {idx} of proxy is undecodable/mismatched: {exc}"
                ) from exc

            # Round-trip must return the exact same frame number.
            back = frame_map.round_trip(idx)
            if back != idx:
                raise ProxyVerificationError(
                    f"frame map does not round-trip at {idx}: got {back}"
                )
            report["checked_frames"].append(
                {"frame": idx, "mae": round(mae, 3), "round_trip_frame": back}
            )
            report["max_mae"] = max(report["max_mae"], mae)

    report["max_mae"] = round(report["max_mae"], 3)
    if report["max_mae"] > mae_limit:
        raise ProxyVerificationError(
            f"proxy content mismatch: max MAE {report['max_mae']} > {mae_limit}; "
            "proxy may be corrupt or derived from different content"
        )
    report["passed"] = True
    report["frame_map"] = frame_map.to_dict()
    return report
