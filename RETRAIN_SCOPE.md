# Retrain flywheel — scope (Part 2, Step 3)

**Goal (scoped narrowly):** raise detection recall on the equipment v10 genuinely *misses* —
**ROOFTOP UNIT, FIRE SMOKE DAMPER, RAIN CAP, RELIEF HOOD, GAS UNIT HEATER, LINEAR SLOT** — via a
targeted retrain, with a benchmark gate so we never ship a regression.

> **Set expectations honestly.** These are *low-count* classes (RTU 6, FSD 26, rain cap 21, relief
> hood 9 in the 12-project truth). The detector is already ~84% on air devices (84% of all equipment),
> so this retrain improves **completeness on roof/specialty equipment**, not the headline recall. It is
> NOT a general accuracy rescue — the air-device win came from subtype-from-tag (no retrain).

## What already exists (most of the loop is built)
- Scripts: `bluebeam_to_yolo.py` (marked PDF → YOLO labels), `learn_from_corrections.py`,
  `prepare_training.py`, `train_v11.py` (fine-tune v10, imgsz 640), `make_colab_bundle.py`.
- Datasets: `yolo_dataset_v11…v16` scaffolds (tiles gitignored — regenerated for Colab). 33-class
  taxonomy already includes the miss targets.
- Labeled data on this machine: **12 Bluebeam-marked PDFs** (`benchmark_manifest.json`; contain the
  miss classes), **6 Label-Studio ground-truth projects** (`ground_truth/`), **3 UI-correction jobs**
  (`saas/data/corrections/`).
- Gate: `run_benchmark_suite.py` + held-out split in `benchmark_manifest.json` (used 2026-06-19).
- Notebook: `colab_train_v16.ipynb`. GPU: none local → **Colab** (care@triunesolutions.com).

## The loop
```
corrections + marked PDFs + ground truth
   → bluebeam_to_yolo / learn_from_corrections  (→ canonical 33-class labels, tiled)
   → make_colab_bundle                          (zip dataset + v10 base + train script)
   → [Colab GPU] train_v11.py                   (fine-tune v10 → candidate)
   → run_benchmark_suite.py (HELD-OUT GATE)      (promote only if it beats v10)
   → set HVAC_MODEL=models/<new>.pt + restart    (deploy)
```

## Phases, effort, and who does what

| Phase | Work | Where | Est. |
|---|---|---|---|
| **0. Diagnose v14** | Why v14 under-detects (held-out recall 0; 242 vs 986 dets) — class-index map? over-aug? too few epochs? Don't retrain until known. | this box | ~0.5 day |
| **1. Curate data** | Convert the 12 marked PDFs + 6 GT + 3 corrections → labels (`bluebeam_to_yolo`); **balance toward the miss classes**; quality over quantity (the v14 lesson). | this box | 1–2 days |
| **2. Build dataset + bundle** | `learn_from_corrections` → tiled dataset (v17); `make_colab_bundle` → zip; upload to Colab. | this box + upload | ~0.5 day |
| **3. Train** | `train_v11.py` fine-tune v10, imgsz 640, tuned aug. | **Colab GPU** (user) | ~2–3 hr GPU |
| **4. Gate** | `run_benchmark_suite.py` candidate vs v10 on held-out. **Promote only if held-out recall ≥ v10.** | this box | ~20 min |
| **5. Deploy** | `HVAC_MODEL=models/<new>.pt`, restart backend. | this box | mins |
| **6. Automate (later)** | UI correction → `training_queue.jsonl` → scheduled retrain → gate → deploy. | — | separate |

**Total ≈ 3–5 days**, mostly data curation + Colab iteration. Token/GPU cost low (Colab Pro).

## Hard rules (the v14 lessons)
1. **Diagnose v14 first** (Phase 0) — or we burn a cycle repeating the regression.
2. **Curate, don't dump** — diverse, *correct* labels beat more data.
3. **Gate on held-out** — never deploy a model that doesn't beat v10 on the held-out split.
4. **One change at a time** — measure each retrain against the same gate.

## Split: this box vs Colab vs you
- **I can do on this box:** Phase 0 (diagnose v14), Phase 1 (convert/curate labels), Phase 2 (build
  dataset + Colab bundle), Phase 4 (run the gate), Phase 5 (deploy).
- **Needs Colab + you:** Phase 3 (GPU training) — run the notebook on care@triunesolutions.com. I prep
  the bundle + notebook so it's one upload-and-run.

## Phase 0 — DONE (2026-06-19): why v14 regressed

Diagnosed from the model checkpoints + a confidence sweep on Barings (113 truth boxes):
- **NOT a class-index bug** — v14's class names AND order are identical to v10.
- **NOT a threshold/calibration artifact** — v14 detects ~half of v10 at EVERY conf (0.40: 44 vs 111;
  0.05: 135 vs 257). Weak weights, not a filter.
- **ROOT CAUSE (training args):** v14 used **lr0=0.0005 (20× too low vs v10's 0.01)** and
  **mosaic=0.0 (disabled vs v10's 0.5)**. Same 60 epochs → barely trained → under-detects everywhere,
  no mosaic → no generalization (held-out recall 0). v14 also still dumps into AD-GRD (failed its own
  goal — and that goal is moot now: subtype comes from the tag, not vision).

**v17 recipe (locked from this diagnosis):**
1. `lr0 ≈ 0.01`, `mosaic = 0.5` (match v10's working recipe) — fixes the under-detection.
2. **Collapse the air-device subclasses** (subtype now comes from the tag/schedule) → fewer confusable
   classes → higher recall.
3. Add positive examples of the true-miss classes (RTU, FSD, rain cap, relief hood).
4. **Gate:** candidate must recover v10's detection count AND beat v10 on held-out, or it doesn't ship.

## Phase 1 — DONE (2026-06-19): label inventory + the data unlock + the blocker

`curate_training_data.py` (current trainable data) + `mine_markups_sample.py` (OneDrive corpus):
- **Trainable now = 1,240 boxes (12 local marked PDFs).** 84% air devices. The miss classes are
  STARVED — ROOFTOP UNIT 6, FIRE SMOKE DAMPER 6, GAS UNIT HEATER 6, RELIEF HOOD 9, RAIN CAP 21.
  **That ~6-examples-each is WHY the model misses them** (YOLO needs dozens+). Data scarcity, not model.
- Also a naming gap: truth "ROOFTOP UNIT" vs model class "PACKAGED ROOFTOP UNIT" → normalize.
- **The unlock:** OneDrive has **1,122 marked Takeoff PDFs** (`Completed Takeoff/Takeoff_*.pdf`) — the
  team's months of takeoffs, a massive label source vs the 12 we use.
- **BLOCKER:** those 1,122 are **OneDrive cloud-only placeholders** (Offline + ReparsePoint attrs;
  fitz → "no objects found"). They must be DOWNLOADED (hydrated) locally before mining.

**Two-track plan out of Phase 1:**
- **Track A (data-ready NOW):** air-device consolidation retrain — collapse AD-* subclasses (subtype
  comes from the tag), restore lr0=0.01/mosaic=0.5. 1,036 air-device boxes locally is plenty. No OneDrive needed.
- **Track B (needs data):** miss-class retrain — hydrate a roof-heavy subset of the 1,122 OneDrive
  marked PDFs (right-click → "Always keep on this device", or a scripted read-to-hydrate), then
  `bluebeam_to_yolo` mines them → hundreds of RTU/FSD/relief-hood examples → balanced dataset.

## Track A — BUILT + Colab-ready (2026-06-19)
- `build_track_a_dataset.py` → `yolo_dataset_v17/` (in-sample only; AD-* collapsed → one "AIR DEVICE"
  class; 26 classes; 1,036 air-device boxes). Held-out (Barings, St.Francis) excluded.
- `tile_v17.py` → `yolo_dataset_v17_tiled/` (810 tiles: 747 train + 63 val).
- **Fixed `train_v11.py`** to v10's recipe (SGD, lr0=0.01, mosaic=0.5) — the v14 bug.
- **Bundle: `colab_bundle/hvac_v17_tiled_colab.zip` (80 MB)** = dataset + v10 base + fixed train script.

**To run (you, on Colab — care@triunesolutions.com):**
1. Upload `hvac_v17_tiled_colab.zip`, unzip.
2. `!python train_v11.py --epochs 60 --device 0 --batch 16 --imgsz 640`
3. Download the resulting `best.pt` → put in `models/hvac_yolov8s_v17.pt`.

**Then (me, on this box):**
4. Gate: `python gate_track_a.py models/hvac_yolov8s_v17.pt` — air-device-aliasing-aware gate on the
   HELD-OUT projects (Track A is collapsed taxonomy, so the raw count gate would unfairly score it 0).
5. If it beats the bar: `HVAC_MODEL=models/hvac_yolov8s_v17.pt`, restart backend. Else: iterate, don't ship.

**LOOP VALIDATED END-TO-END (2026-06-19, CPU smoke run):** build → tile → train (fixed recipe) → model
→ `gate_track_a.py` → verdict all run clean. A 2-epoch CPU smoke model scored F1 0.225 and the gate
correctly said KEEP v10. So the Colab run is just "same commands, GPU, 60 epochs."

**THE BAR TO BEAT (v10, held-out, air-device aliased): recall 0.842 · precision 0.941 · F1 0.889.**
v17 must exceed F1 0.889 on `gate_track_a.py` to ship.

**Honest caveat:** 810 tiles from 10 projects is SMALL (v10 used ~25K). Track A may overfit / not beat
v10 — the held-out gate decides. The collapse should help air-device recall; it will NOT fix the
true-misses (those need Track B / OneDrive data). Treat as an experiment with a hard gate.

## Track B — PREPPED (tools ready; gated on the OneDrive download)
Corpus: **1,122 marked Takeoff PDFs in OneDrive, ~21.9 GB, 1,120 cloud-only** (Offline placeholders).
**Blocker: OneDrive isn't running** → the cloud file provider must be up to hydrate anything.

Tools built + validated (dry-run):
- `hydrate_track_b.ps1` — pins (downloads) marked PDFs. `-Status` / dry-run / `-Sample N -Execute` / `-All -Execute`.
- `build_track_b.py` — mines HYDRATED PDFs → `yolo_dataset_v18_tiled/` (collapsed taxonomy, flat layout,
  640px tiles), **excludes the 12 benchmark projects (leakage guard)**, reports miss-class gains.

**Run order (you + me):**
1. Start OneDrive: `Start-Process "C:\Program Files\Microsoft OneDrive\OneDrive.exe"` (sign in, wait ~30s).
2. Hydrate a sample first (don't pull all 22 GB blind):
   `powershell -ExecutionPolicy Bypass -File hydrate_track_b.ps1 -Sample 150 -Execute` (~3.5 GB).
   Watch: `... -Status` until cloud-only drops.
3. Mine: `python build_track_b.py` → see how many RTU/FSD/relief-hood examples the sample yielded.
4. If the miss classes now have dozens+ examples → `make_colab_bundle.py yolo_dataset_v18_tiled` → Colab
   train → `gate_track_a.py` (must beat F1 0.889). If still thin → hydrate more (`-Sample 400` or `-All`).

This is the real fix for the true-misses (RTU/FSD/relief hood/gas heater) that Track A can't address.

### Track B — FIRST MINE DONE (2026-06-20)
Started OneDrive + hydrated a sample; `build_track_b.py` mined **105 projects → 18,814 tiles**
(`yolo_dataset_v18_tiled`, 1.7 GB). Miss classes now LEARNABLE (vs ~6 each before):
FIRE SMOKE DAMPER 6→1030 · VENT CAP 26→1100 · LOUVER 8→557 · PACKAGED ROOFTOP UNIT 6→129 · HOOD 9→93.
AIR DEVICE 21,530 · FAN 1,334. Leakage guard skipped 124 benchmark-matching PDFs (keyword-based, a bit
over-aggressive — refine later) + 1,127 not-yet-hydrated.
- **Bundle ready: `colab_bundle/hvac_v18_tiled_colab.zip` (1.7 GB)** — v18 supersedes v17 (it already
  contains air devices + all classes). Includes v10 base + fixed train_v11.py.
- RTU still thinnest (129) — hydrate more (`hydrate_track_b.ps1 -Sample 400 -Execute`) + re-mine to add.
- **Next:** upload v18 bundle to Colab → `train_v11.py --epochs 60 --device 0 --batch 16` → drop best.pt as
  `models/hvac_yolov8s_v18.pt` → `python gate_track_a.py models/hvac_yolov8s_v18.pt` (must beat F1 0.889).
