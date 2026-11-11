"""Thin wrapper around the ffmpeg / ffprobe command line tools.

Binaries are located with the PROXYWS_FFMPEG / PROXYWS_FFPROBE environment
variables, then the bundled ``tools/`` directory, then ``PATH``. Every wrapper
checks the return code and surfaces stderr so decode failures never pass
silently (a corrupt proxy must be detected, not assumed valid).
"""

import json
import os
import shutil
import subprocess

from .errors import MediaReadError


def _candidate(names, env_var):
    env = os.environ.get(env_var)
    if env and os.path.isfile(env):
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (
        os.path.join(here, "..", "..", "tools"),
        os.path.join(here, "..", "..", "..", "tools"),
    ):
        p = os.path.normpath(os.path.join(base, names[0]))
        if os.path.isfile(p):
            return p
    found = shutil.which(names[0])
    if found:
        return found
    raise MediaReadError(
        f"{names[0]} binary not found; set {env_var} to its full path"
    )


def ffmpeg_bin():
    return _candidate(("ffmpeg",), "PROXYWS_FFMPEG")


def ffprobe_bin():
    return _candidate(("ffprobe",), "PROXYWS_FFPROBE")


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-6:]
        raise MediaReadError(
            f"command failed ({proc.returncode}): {' '.join(cmd[:2])} ...\n"
            + "\n".join(tail)
        )
    return proc


def probe(path):
    """Return a normalized media descriptor: stream/video metadata and frame count."""
    if not os.path.isfile(path):
        raise MediaReadError(f"file not found: {path}")
    cmd = [
        ffprobe_bin(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-count_frames",
        path,
    ]
    try:
        data = json.loads(_run(cmd).stdout)
    except MediaReadError:
        raise
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise MediaReadError(f"cannot parse ffprobe output for {path}: {exc}")

    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaReadError(f"no video stream in {path}")

    fps_text = video.get("avg_frame_rate", "0/0")
    num, den = fps_text.split("/")
    fps = int(num) / int(den) if int(den) else 0.0
    duration = float(data["format"].get("duration", 0.0) or 0.0)

    nb_frames = video.get("nb_read_frames") or video.get("nb_frames")
    if nb_frames in (None, "N/A"):
        nb_frames = round(duration * fps) if fps else 0
    else:
        nb_frames = int(nb_frames)

    return {
        "path": os.path.abspath(path),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "frame_count": nb_frames,
        "duration": duration,
        "codec": video.get("codec_name", ""),
        "pix_fmt": video.get("pix_fmt", ""),
    }


def make_proxy(src_path, dst_path, height, crf=23, preset="veryfast"):
    """Create a time-preserving proxy.

    ``-vsync passthrough`` keeps exactly one output frame per input frame and
    no start offset is introduced; the filter only downscales. This is the
    only transform V1 knows how to map back exactly.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-v",
        "error",
        "-i",
        src_path,
        "-vf",
        f"scale=-2:{int(height)}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-an",
        "-vsync",
        "passthrough",
        dst_path,
    ]
    _run(cmd)
    return dst_path


def full_decode_check(path):
    """Decode every frame to null. Raises MediaReadError on any corrupt NAL,
    truncated moov or container damage — content-frame comparison alone can
    miss corruption that ffmpeg's error concealment hides."""
    cmd = [
        ffmpeg_bin(),
        "-v",
        "error",
        "-xerror",
        "-i",
        path,
        "-f",
        "null",
        "-",
    ]
    _run(cmd)
    return True


def extract_frame(path, frame_index, out_path, fps):
    """Decode one exact frame by frame number to PNG."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-v",
        "error",
        "-i",
        path,
        "-vf",
        f"select=eq(n\\,{int(frame_index)})",
        "-vsync",
        "0",
        "-frames:v",
        "1",
        out_path,
    ]
    _run(cmd)
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise MediaReadError(f"frame {frame_index} could not be decoded from {path}")
    return out_path


def render_concat(segments, out_path, width, height, fps, crf=18, preset="medium"):
    """Render a final cut from original sources.

    ``segments`` is a list of (source_path, start_frame, end_frame_exclusive).
    Each segment is trimmed frame-accurately with select, normalized to the
    output size/fps and concatenated via the filter graph.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cmd = [ffmpeg_bin(), "-y", "-v", "error"]
    for src, _, _ in segments:
        cmd += ["-i", src]

    filters = []
    labels = []
    for idx, (_, start, end) in enumerate(segments):
        # trim works on frame numbers and setpts closes the timestamp gap,
        # which keeps one output frame per selected frame (select+fps would
        # invent duplicate frames to fill the dropped time range).
        filters.append(
            f"[{idx}:v]trim=start_frame={int(start)}:end_frame={int(end)},"
            f"setpts=PTS-STARTPTS,"
            f"scale={int(width)}:{int(height)}:force_original_aspect_ratio=decrease,"
            f"pad={int(width)}:{int(height)}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1[v{idx}]"
        )
        labels.append(f"[v{idx}]")
    filters.append("".join(labels) + f"concat=n={len(segments)}:v=1[vout]")

    cmd += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        str(preset),
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        out_path,
    ]
    _run(cmd)
    return out_path
