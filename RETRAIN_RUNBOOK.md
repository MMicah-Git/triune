# v19s + demo-3 Retrain — Detailed Runbook

Goal: train a detection model that learns the equipment v10 misses (RTU, CU,
MVD, split-system), **including your 3 projects** (PNC Medical, Atascocita,
CityVet), then gate it against v10 and deploy if it wins.

---

## 0. Current state (already done)
- Dataset built: `yolo_dataset_v19s_tiled` = 10,306 tiles (10,000 base + 306 from
  your 3 takeoffs, 4× oversampled; PNC held for validation).
- Bundle ready: `colab_bundle/hvac_v19s_demo3_colab.zip` (**1002 MB**, verified).
- Notebook ready: `colab_train_v19s_upload.ipynb` (has a Google-Drive backup cell).
- Watcher running locally: waits for `hvac_yolov8s_v19s.pt`, then auto-runs the gate.
- Backend live on :8000, app on :3000.

You only need to run the Colab job and get the file down. Everything else is automatic.

---

## 1. Open the notebook in Colab
1. Go to https://colab.research.google.com
2. Sign in with the Google account that has your Drive (the care@ account).
3. **File → Upload notebook** → choose:
   `…\hvac-takeoff-tool-master\colab_train_v19s_upload.ipynb`

## 2. Enable the GPU
1. **Runtime → Change runtime type → Hardware accelerator: GPU → Save.**
2. Run **Cell 1**. It must print a GPU name (e.g. `Tesla T4`). If it says `NONE`,
   the GPU isn't on — redo this step.
3. Run **Cell 2** (installs `ultralytics`, ~30 s).

## 3. Upload the bundle (the 1002 MB one)
1. Click the **folder icon** on the left sidebar (file browser).
2. **Drag** `…\colab_bundle\hvac_v19s_demo3_colab.zip` into that panel.
   - It MUST be the **1002 MB** file (that's the one with your 3 projects).
   - NOT the older 940 MB `hvac_v19s_tiled_colab.zip`.
3. Wait for the upload spinner to finish (~5–20 min depending on your upload speed).
   Don't run the next cell until it's done.

## 4. Train
1. Run **Cell 3** (unzips the bundle into /content/hvac).
2. Run **Cell 4** (training). **Verify the first lines print:**
   `Dataset:  yolo_dataset_v19s_tiled/data.yaml`
   If it says anything else, stop — wrong bundle.
3. Let it run to **`60/60`** (~60–90 min on a T4). You'll see per-epoch lines;
   losses should drift down. You can ignore the mAP numbers — the real gate is local.

## 5. Get the weight out (this produces hvac_yolov8s_v19s.pt)
1. Run **Cell 5 (Drive backup — do this FIRST):** it mounts your Drive
   (click through the auth: pick your account → Allow) and copies the weight to
   `MyDrive/hvac_models/hvac_yolov8s_v19s.pt`. This copy CANNOT be lost.
2. Run **Cell 6 (direct download):** if Chrome warns "this type of file can harm
   your computer," click **Keep** or it won't save.
3. Make sure the file lands in **`C:\Users\TriuneTakeoff\Downloads\`**
   (named exactly `hvac_yolov8s_v19s.pt`). If you pulled it from Drive instead,
   download it from `MyDrive/hvac_models/` to that Downloads folder.

## 6. Automatic from here
- The watcher sees the file (within ~60 s), copies it into `models/`, and runs
  `gate_track_a.py` — scoring v19s+demo3 vs v10 on the **held-out** benchmark
  (Barings / St Francis — different files, so the gate is honest).
- That pings Claude. Claude reports:
  - **PASS** (F1 > 0.889) → deploy it (set `HVAC_MODEL`) + restart backend.
  - **FAIL** → keep v10, and we read why (likely needs more/broader data).

---

## What to expect (honest)
- On your 3 files specifically, RTU/CU/MVD detection should improve a lot — they're
  now in the training set.
- The held-out gate is the real test of generalization. The earlier small-data
  attempt (v19xs) scored F1 0.843 < 0.889 and was rejected. This bundle is bigger
  and includes the miss-classes, so it has a better shot — but it's not guaranteed
  to clear v10 in one run. If it doesn't, the recipe's fine; it means more data.
- Even after deploy, the per-instance neck-size split + counts are draft-quality;
  the estimator still verifies. This retrain mainly closes the detection-coverage gap.

## Troubleshooting
- **Colab disconnects mid-train:** re-run Cells 4–5. Don't close the tab; keep the
  laptop awake (sleep is what killed earlier runs).
- **".pt won't download":** use the Drive copy from Cell 5 instead.
- **Watcher timed out (12 h):** tell Claude "restart the watcher."
- **Wrong dataset line in Cell 4:** you uploaded the wrong zip — re-upload the 1002 MB one.

## Commands Claude runs (for reference)
- Gate: `python gate_track_a.py models/hvac_yolov8s_v19s.pt`
- Deploy (on pass): set `HVAC_MODEL=models/hvac_yolov8s_v19s.pt`, restart backend.
