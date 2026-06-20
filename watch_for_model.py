"""
watch_for_model.py — wait for the Colab-trained model to land, then auto-run the gate.

Polls for models/hvac_yolov8s_v19.pt (or v18). When it appears AND its size is
stable (finished copying), runs gate_track_a.py and prints the verdict, then exits.
"""
import sys, os, time, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CANDIDATES = ['models/hvac_yolov8s_v19.pt', 'models/hvac_yolov8s_v18.pt']
POLL_S = 60
MAX_HRS = 12


def stable_model():
    for rel in CANDIDATES:
        p = ROOT / rel
        if p.exists():
            s1 = p.stat().st_size
            time.sleep(3)
            if s1 > 1_000_000 and p.stat().st_size == s1:   # >1MB and not still growing
                return rel
    return None


def main():
    start = time.time()
    print(f"watching for trained model {CANDIDATES} (poll {POLL_S}s, max {MAX_HRS}h)", flush=True)
    while True:
        m = stable_model()
        mins = int((time.time() - start) / 60)
        if m:
            print(f"[{mins}m] found {m} — running the gate...", flush=True)
            r = subprocess.run([sys.executable, 'gate_track_a.py', m], cwd=str(ROOT))
            print(f"GATE DONE (exit {r.returncode}) on {m}.", flush=True)
            return
        print(f"[{mins}m] not here yet...", flush=True)
        if time.time() - start > MAX_HRS * 3600:
            print(f"max {MAX_HRS}h reached — no model arrived. Re-run watch_for_model.py later.", flush=True)
            return
        time.sleep(POLL_S)


if __name__ == '__main__':
    main()
