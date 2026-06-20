"""
auto_remine.py — watch OneDrive hydration; when enough marked PDFs are local,
auto re-mine the larger corpus into a fresh dataset + Colab bundle.

Polls the count of LOCAL (hydrated) marked Takeoff PDFs (attribute check only —
never opens placeholders). When it crosses THRESHOLD, runs build_track_b.py
(--out v19) then make_colab_bundle.py, then exits. Safety cap on total runtime.

Usage:  python auto_remine.py [threshold] [out_dataset]
"""
import sys, os, time, glob, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OD = Path.home() / 'OneDrive - Triune Solutions LLC'
OFFLINE, RECALL = 0x1000, 0x00400000
THRESH = int(sys.argv[1]) if len(sys.argv) > 1 else 300
OUTDS = sys.argv[2] if len(sys.argv) > 2 else 'yolo_dataset_v19_tiled'
POLL_S = 120
MAX_HRS = 10


def local_count() -> int:
    n = 0
    for p in glob.glob(str(OD / '**' / 'Takeoff_*.pdf'), recursive=True):
        try:
            if not (os.stat(p).st_file_attributes & (OFFLINE | RECALL)):
                n += 1
        except Exception:
            pass
    return n


def main():
    start = time.time()
    print(f"watching hydration — re-mine when local marked PDFs >= {THRESH} "
          f"(poll {POLL_S}s, max {MAX_HRS}h) -> {OUTDS}", flush=True)
    while True:
        c = local_count()
        mins = int((time.time() - start) / 60)
        print(f"[{mins}m] local marked PDFs: {c}/{THRESH}", flush=True)
        if c >= THRESH:
            print(f"[{mins}m] THRESHOLD reached ({c}) — re-mining into {OUTDS}...", flush=True)
            r = subprocess.run([sys.executable, 'build_track_b.py', '--out', OUTDS],
                               cwd=str(ROOT))
            print(f"  build_track_b exit={r.returncode}", flush=True)
            if r.returncode == 0:
                r2 = subprocess.run([sys.executable, 'make_colab_bundle.py', OUTDS], cwd=str(ROOT))
                print(f"  make_colab_bundle exit={r2.returncode}", flush=True)
            print("AUTO-REMINE DONE.", flush=True)
            return
        if time.time() - start > MAX_HRS * 3600:
            print(f"max runtime {MAX_HRS}h reached at local={c} — stopping watch "
                  f"(download too slow). Re-run build_track_b.py manually later.", flush=True)
            return
        time.sleep(POLL_S)


if __name__ == '__main__':
    main()
