"""Generate synthetic animation test media.

Each frame is a solid color with a large visible frame number and shot label,
so frame-accurate mapping can be checked visually and by pixel content. Two
resolutions (960x540 and 1280x720) exercise proxy downscale paths. The
generator also produces a *same name, different content* decoy to prove that
identity is hash-based rather than filename-based.
"""

import argparse
import os
import site
import subprocess
import sys
import tempfile

# Stay importable under `python3 -I` (isolated mode skips user site-packages).
import glob

for _candidate in glob.glob(
    os.path.expanduser("~/.local/lib/python*/site-packages")
):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        site.addsitedir(_candidate)

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxyworkstation.core.ffutils import ffmpeg_bin  # noqa: E402

PALETTE = {
    "SHOT_A": (32, 96, 190),
    "SHOT_B": (190, 64, 64),
    "SHOT_C": (48, 150, 90),
}


def render_frames(shot_id, width, height, frame_count, fps, tmpdir, tag=""):
    base = PALETTE.get(shot_id, (90, 90, 90))
    for n in range(frame_count):
        img = Image.new("RGB", (width, height), base)
        draw = ImageDraw.Draw(img)
        # Moving band gives every frame distinct pixel content.
        band_x = int((n / max(1, frame_count - 1)) * (width - 120))
        draw.rectangle([band_x, 0, band_x + 120, height], fill=(255, 255, 255))
        label = f"{shot_id}{(' ' + tag) if tag else ''}"
        draw.text((24, 18), label, fill=(255, 255, 0))
        draw.text((24, height // 2 - 40), f"FRAME {n:04d}", fill=(0, 0, 0))
        draw.text((24, height // 2 + 20), f"{width}x{height} @ {fps}fps", fill=(0, 0, 0))
        img.save(os.path.join(tmpdir, f"f_{n:05d}.png"))


def encode(tmpdir, out_path, fps):
    cmd = [
        ffmpeg_bin(), "-y", "-v", "error",
        "-framerate", str(fps),
        "-i", os.path.join(tmpdir, "f_%05d.png"),
        "-c:v", "libx264", "-preset", "medium", "-crf", "16",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def make_clip(shot_id, out_path, width, height, frame_count=24, fps=24, tag=""):
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sample_frames_") as tmp:
        render_frames(shot_id, width, height, frame_count, fps, tmp, tag)
        encode(tmp, out_path, fps)
    return out_path


def generate_all(root):
    """Create the standard sample set under ``root``. Returns a manifest dict."""
    originals = os.path.join(root, "originals")
    decoy_dir = os.path.join(root, "decoy")
    manifest = {}

    specs = [
        ("SHOT_A", 960, 540, 24),
        ("SHOT_B", 1280, 720, 30),
        ("SHOT_C", 960, 540, 18),
    ]
    for shot_id, w, h, count in specs:
        path = os.path.join(originals, f"{shot_id}.mp4")
        make_clip(shot_id, path, w, h, frame_count=count)
        manifest[shot_id] = path

    # Same filename as SHOT_A's media but visibly different content.
    decoy = os.path.join(decoy_dir, "SHOT_A.mp4")
    make_clip("SHOT_A", decoy, 960, 540, frame_count=24, tag="DECOY-DIFFERENT-CONTENT")
    manifest["DECOY_SAME_NAME"] = decoy
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate sample media")
    parser.add_argument("out_dir", help="directory to write samples into")
    args = parser.parse_args()
    result = generate_all(args.out_dir)
    for name, path in result.items():
        print(f"{name}: {path}")
