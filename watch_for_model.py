"""
watch_for_model.py — wait for the Colab-trained model to land, then auto-run the gate.

Watches the likely drop locations (the browser Downloads folder, the repo root,
and models/) for the trained weight. When it appears AND its size is stable
(finished copying/downloading), copies it into models/ and runs gate_track_a.py,
prints the promote/keep verdict (bar = v10 held-out F1 0.889), then exits.

Default target is v19s (the 940 MB full-air-device retrain). Override:
    python watch_for_model.py hvac_yolov8s_v19s.pt
"""
import sys, os, time, shutil, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOWNLOADS = Path(os.path.expanduser('~')) / 'Downloads'
# search these dirs, in priority order, for the target filename
SEARCH_DIRS = [ROOT / 'models', ROOT, DOWNLOADS]
# filenames to accept (first one given on the CLI wins; else this default list)
DEFAULT_NAMES = ['hvac_yolov8s_v19s.pt', 'hvac_yolov8s_v19.pt', 'hvac_yolov8s_v18.pt']
POLL_S = 60
MAX_HRS = 12


def find_stable(names):
    """Return (src_path, name) of the first stable matching weight, else None."""
    for name in names:
        for d in SEARCH_DIRS:
            p = d / name
            if p.exists():
                s1 = p.stat().st_size
                time.sleep(3)
                if s1 > 1_000_000 and p.stat().st_size == s1:   # >1MB and not still growing
                    return p, name
    return None


def main():
    names = [sys.argv[1]] if len(sys.argv) > 1 else DEFAULT_NAMES
    start = time.time()
    print(f"watching {[str(d) for d in SEARCH_DIRS]}", flush=True)
    print(f"for {names} (poll {POLL_S}s, max {MAX_HRS}h)", flush=True)
    while True:
        hit = find_stable(names)
        mins = int((time.time() - start) / 60)
        if hit:
            src, name = hit
            dst = ROOT / 'models' / name
            if src.resolve() != dst.resolve():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                print(f"[{mins}m] found {src} -> copied to {dst}", flush=True)
            else:
                print(f"[{mins}m] found {dst}", flush=True)
            rel = f"models/{name}"
            print(f"[{mins}m] running the gate on {rel} ...", flush=True)
            r = subprocess.run([sys.executable, 'gate_track_a.py', rel], cwd=str(ROOT))
            print(f"GATE DONE (exit {r.returncode}) on {rel}.", flush=True)
            print("If the verdict is PROMOTE: deploy with HVAC_MODEL=models/%s and restart the backend." % name, flush=True)
            return
        print(f"[{mins}m] not here yet...", flush=True)
        if time.time() - start > MAX_HRS * 3600:
            print(f"max {MAX_HRS}h reached — no model arrived. Re-run watch_for_model.py later.", flush=True)
            return
        time.sleep(POLL_S)


if __name__ == '__main__':
    main()
