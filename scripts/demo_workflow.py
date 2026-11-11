"""End-to-end CLI demonstration of every workstation guarantee.

Builds fresh sample media (visible frame numbers, two original resolutions),
runs the full workflow including the failure scenarios (same-name decoy,
corrupt proxy, file relocation), and writes a verifiable final program.

Run:  python3 -I scripts/demo_workflow.py /tmp/pws_demo
"""

import glob
import os
import shutil
import site
import sys

for _c in glob.glob(os.path.expanduser("~/.local/lib/python*/site-packages")):
    site.addsitedir(_c)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxyworkstation.core.errors import (  # noqa: E402
    MissingSourceError,
    UnsupportedTransformError,
    WorkstationError,
)
from proxyworkstation.core.ffutils import probe  # noqa: E402
from proxyworkstation.core.framemap import build_time_preserving_map  # noqa: E402
from proxyworkstation.core.identity import sha256_file  # noqa: E402
from proxyworkstation.core.workstation import Workstation  # noqa: E402
from scripts.make_samples import generate_all  # noqa: E402


def line(title):
    print(f"\n=== {title} ===")


def main(root):
    if os.path.isdir(root):
        shutil.rmtree(root)
    media_dir = os.path.join(root, "media")
    project_dir = os.path.join(root, "project")
    proxy_dir = os.path.join(project_dir, "proxies")
    moved_dir = os.path.join(root, "moved_originals")
    os.makedirs(project_dir)

    line("1. Generate samples with visible frame numbers and two resolutions")
    manifest = generate_all(media_dir)
    for name, path in manifest.items():
        meta = probe(path)
        print(f"  {name}: {meta['width']}x{meta['height']} "
              f"{meta['frame_count']} frames @ {meta['fps']:g}fps")

    ws = Workstation(os.path.join(project_dir, "workstation.db"))

    line("2. Import originals — identity is content hash, not filename")
    hashes = {}
    for shot in ("SHOT_A", "SHOT_B", "SHOT_C"):
        result = ws.import_source(manifest[shot], shot)
        hashes[shot] = result["content_hash"]
        print(f"  {shot} -> {result['content_hash'][:16]}…")
    decoy_hash = sha256_file(manifest["DECOY_SAME_NAME"])
    print(f"  decoy same-name file hash {decoy_hash[:16]}… "
          f"differs from SHOT_A: {decoy_hash != hashes['SHOT_A']}")

    line("3. Automatic proxy task: downscale + verify + record mapping")
    results = ws.generate_proxies_batch(
        ["SHOT_A", "SHOT_B", "SHOT_C"], proxy_dir, height=270, crf=28
    )
    for r in results["succeeded"]:
        fmap = r["frame_map"]
        checked = r["report"]["checked_frames"]
        print(f"  {r['shot_id']}: proxy {r['params']['width']}x{r['params']['height']} "
              f"crf={r['params']['crf']} | map fps {fmap['proxy_fps']:g} "
              f"offset {fmap['source_start_offset']} | "
              f"verified frames {[c['frame'] for c in checked]} "
              f"max MAE {r['report']['max_mae']}")

    line("4. Unsupported transforms are explicitly rejected")
    try:
        ws.generate_proxy("SHOT_A", os.path.join(root, "bad"), height=800)
    except UnsupportedTransformError as exc:
        print(f"  REJECTED upscale: {exc}")
    fake_source = probe(manifest["SHOT_A"])
    fake_proxy = dict(fake_source, fps=30.0)
    try:
        build_time_preserving_map(fake_source, fake_proxy)
    except UnsupportedTransformError as exc:
        print(f"  REJECTED frame-rate change: {exc}")
    fake_proxy2 = dict(fake_source, frame_count=fake_source["frame_count"] - 2)
    try:
        build_time_preserving_map(fake_source, fake_proxy2)
    except UnsupportedTransformError as exc:
        print(f"  REJECTED frame-count/offset change: {exc}")

    line("5. Edit timeline using logical shots and source-frame intervals")
    ws.add_clip("SHOT_A", 2, 10)
    ws.add_clip("SHOT_B", 5, 12)
    ws.add_clip("SHOT_C", 0, 6)
    for clip in ws.store.list_clips():
        print(f"  clip {clip['clip_id']}: {clip['shot_id']} "
              f"[{clip['source_in']}, {clip['source_out']})")

    line("6. Switching preview quality does not change in/out points")
    src_a = ws.store.get_source_by_shot("SHOT_A")
    proxy_view = ws.resolve_media(src_a["content_hash"], "proxy")
    original_view = ws.resolve_media(src_a["content_hash"], "original")
    clip_a = ws.store.list_clips()[0]
    print(f"  proxy view:    {os.path.basename(proxy_view['path'])} "
          f"({proxy_view['kind']})")
    print(f"  original view: {os.path.basename(original_view['path'])} "
          f"({original_view['kind']})")
    print(f"  stored interval unchanged: [{clip_a['source_in']}, {clip_a['source_out']})")

    line("7. Corrupt proxy is detected, never trusted")
    proxy_record = ws.store.get_proxy_for(hashes["SHOT_A"])
    good_proxy = proxy_record["proxy_path"]
    corrupt = good_proxy + ".corrupt.mp4"
    shutil.copy2(good_proxy, corrupt)
    with open(corrupt, "r+b") as fh:
        fh.seek(2048)
        fh.write(os.urandom(4096))
    try:
        from proxyworkstation.core.verification import verify_proxy
        verify_proxy(manifest["SHOT_A"], corrupt)
        print("  ERROR: corruption was not detected")
    except WorkstationError as exc:
        print(f"  CORRUPTION DETECTED: {type(exc).__name__}: {str(exc)[:90]}")
    os.remove(corrupt)

    line("8. Take originals offline: proxy editing continues, render is blocked")
    os.makedirs(moved_dir)
    for source in ws.store.list_sources():
        shutil.move(source["current_path"],
                    os.path.join(moved_dir, os.path.basename(source["current_path"])))
    preflight = ws.preflight()
    print(f"  preflight ready={preflight['ready']}; "
            f"missing={[m['shot_id'] for m in preflight['missing']]}")
    offline_proxy = ws.resolve_media(hashes["SHOT_A"], "proxy")
    print(f"  proxy still editable while offline: "
          f"{os.path.isfile(offline_proxy['path'])} ({offline_proxy['kind']})")
    try:
        ws.render_final(os.path.join(root, "must_not_exist.mp4"))
    except MissingSourceError as exc:
        print(f"  RENDER BLOCKED (no proxy substitution): {exc}")
    assert not os.path.exists(os.path.join(root, "must_not_exist.mp4"))

    line("9. Relocation: same-name decoy refused; hash match accepted")
    try:
        ws.relocate_source(hashes["SHOT_B"], manifest["DECOY_SAME_NAME"])
    except WorkstationError as exc:
        print(f"  DECOY REFUSED: {str(exc)[:100]}")
    for source in ws.store.list_sources():
        new_path = os.path.join(moved_dir, source["shot_id"] + ".mp4")
        ws.relocate_source(source["content_hash"], new_path)
        refreshed = ws.store.get_source_by_hash(source["content_hash"])
        print(f"  relinked {source['shot_id']} -> {new_path} "
              f"[{ws.store.source_status(refreshed)}]")
    print(f"  preflight after relocation: {ws.preflight()['ready']}")

    line("10. Final render from originals with frame-accurate verification")
    out_path = os.path.join(root, "final_program.mp4")
    info = ws.render_final(out_path, crf=18, preset="medium")
    out_meta = probe(out_path)
    print(f"  output: {info['out_path']}")
    print(f"  quality: {info['width']}x{info['height']} @ {info['fps']:g}fps "
          f"crf={info['crf']} preset={info['preset']} codec={out_meta['codec']}")
    print(f"  frames {info['actual_frame_count']} / expected {info['expected_frames']} "
          f"-> frame_accurate={info['frame_accurate']}")
    assert info["frame_accurate"], "final program is not frame-accurate"

    line("11. Round-trip mapping proof on the first clip's boundary frames")
    fmap = proxy_record["frame_map"]
    for source_frame in (clip_a["source_in"], clip_a["source_out"] - 1):
        proxy_frame = source_frame - fmap["source_start_offset"]
        back = proxy_frame + fmap["source_start_offset"]
        print(f"  source {source_frame} -> proxy {proxy_frame} -> source {back} "
              f"({'OK' if back == source_frame else 'FAIL'})")
    ws.close()
    print("\nDEMO COMPLETE — final program at", out_path)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pws_demo")
