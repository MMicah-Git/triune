# Session Summary — June 22-23, 2026

What we did this session, the current state, and what's left. Everything below is
committed to git (`MMicah-Git/triune` master).

---

## The goal
Make the tool, given a RAW plan PDF, produce output close to the team's completed
manual takeoff (Excel + Bluebeam markups) — for 3 real projects: **PNC Medical**,
**PNC Atascocita**, **CityVet Verrado** (each has a completed takeoff as ground truth).

## What we shipped (all live, backend restarted)
1. **Page selection fix** — stopped dropping schedule-heavy floor plans (rotated/
   unreadable title blocks read equipment tags as sheet numbers). PNC went 2 → 48
   detections. (`page_selector.py`)
2. **Excel column fix** — FACE SIZE now lands in MODULE SIZE (was wrongly in NECK);
   DUCT fills; MOUNTING derived from the description. (`takeoff_cli.py`)
3. **Tag normalization** — EF1 == EF-1 matching. (`schedule_parser.py canonical_tag`)
4. **Junk-tag cleanup** — drop sheet-detail refs (M501-9, M501-PROVIDE) + note words
   (MINIMUM, ETC, HZ, V, BARS...). (`schedule_parser.py`)
5. **Schedule-OCR trigger** — fires when the text layer gives <5 tags (broken-font),
   not just zero. (CityVet still defeats OCR — broken font is the hard case.)
6. **Neck-size reader (NEW feature)** — reads per-instance neck/duct sizes off the
   plan callouts (8"Ø, 12X6). Key fix: detection px are in DISPLAY space, text in
   MEDIABOX — bridged with the page derotation matrix. 100% callout coverage on PNC,
   round-vs-rect shape disambiguation, HIGH/MED/LOW confidence. (`saas/backend/neck_size_reader.py`)
7. **Excel split-by-neck-size** — S1 now splits into 6"/8" rows like the team's takeoff.
   (`takeoff_cli.py write_excel`, runs in core pipeline before write_excel)
8. **Bluebeam custom columns (NEW)** — reverse-engineered the team's `BSIColumnData`
   format; our stamped PDF now populates the Markup List columns (BRAND/MODEL/NECK/
   TYPE/MOUNTING/MODULE/DUCT) + Label=tag, matching their manual takeoff.
   (`saas/backend/write_bluebeam_stamps.py`)

## The v19s + demo-3 RETRAIN (set up, not yet run)
The remaining accuracy gap is DETECTION (counts low, RTU/CU/MVD not detected). The
retrain is the fix and it's **set up with the 3 files included**:
- Mined the 3 completed takeoffs → 228 ground-truth boxes (incl 60 MVD, 4 RTU,
  10 split-system) via `bluebeam_to_yolo.py`.
- Collapsed + tiled + merged into `yolo_dataset_v19s_tiled` (10,000 → 10,306 tiles,
  4× oversampled; PNC held for val). (`build_demo3_merge.py`)
- Bundle: **`colab_bundle/hvac_v19s_demo3_colab.zip` (~956 MB)** — gitignored.
- Notebook: **`colab_train_v19s_upload.ipynb`** (30-epoch quick pass, Drive backup cell).
- Full steps: **`RETRAIN_RUNBOOK.md`**.
- Gate stays valid: `gate_track_a.py` scores on held-out projects (not these 3).

## How to run it (short)
1. Colab → upload `colab_train_v19s_upload.ipynb` → GPU runtime → cells 1-2.
2. Drag in `hvac_v19s_demo3_colab.zip` (the 956 MB one) → cells 3-4 (train ~30-45 min).
3. Cell 5 (Drive backup) → cell 6 (download, click Keep) → file to `Downloads\`.
4. The local watcher catches `hvac_yolov8s_v19s.pt`, runs the gate, reports.

## The 3 files — honest status (current v10)
- **PNC Medical** ✅ & **Atascocita** ✅: layout + neck detail now match the completed
  takeoff; air-device count close (45 vs 43). Big units (RTU/CU) listed "verify",
  not marked. ~30 air devices detected-but-untagged (lumped).
- **CityVet Verrado** ❌: broken-font CAD export — schedule unreadable even by OCR.
  Frame as the "hard case" (see `CITYVET_HARD_CASE.md`).

## Honest gaps that ONLY the retrain (or more work) closes
- Counts lower than human (detection recall) → retrain.
- RTU/CU/MVD not marked on plan (not detected) → retrain.
- ~30 air devices untagged/lumped → tag-reading (bubble OCR) work.
- Neck association in dense areas (S4↔S5 swap) → needs per-instance ground truth.
- CityVet broken font → fallback parser (future).

## Workflow that worked: diff-and-fix
Show a completed takeoff vs the tool's output → Claude fixes every CODE/parser bug it
reveals (this caught junk tags, wrong size column, dropped plan, missing neck split).
Counts/detection improve via the retrain, not code. Tool: compare the two Excels by
(tag, neck) — see the session for the comparison script.

## Current running state (end of session)
- Backend: live on :8000 (HVAC_INPROCESS=1), app on :3000. All fixes loaded.
- Watcher: running, waiting for `hvac_yolov8s_v19s.pt` to auto-gate.
- Latest fresh PNC job with all features: `saas/data/jobs/25b98144e85b/`
  (open `*_bluebeam_stamped.pdf` in Bluebeam to see the populated Markup List).
- Repo: all committed + pushed to `MMicah-Git/triune` master.

## Key files added/changed this session
- NEW: `neck_size_reader.py`, `build_demo3_merge.py`, `neck_validate.py`,
  `RETRAIN_RUNBOOK.md`, `CITYVET_HARD_CASE.md`, `part3_diagnostic.py`.
- CHANGED: `page_selector.py`, `takeoff_cli.py`, `schedule_parser.py`,
  `validation_engine.py`, `saas/backend/{post_takeoff,write_bluebeam_stamps}.py`,
  `colab_train_v19s_upload.ipynb`.
