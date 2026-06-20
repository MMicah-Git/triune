# HVAC Takeoff — System Divided into 4 Parts (fix + training plan)

Purpose: split the codebase into independent parts so each can be fixed and improved on its
own, and define the **workflow that makes each part accurate** (training vs. test-driven).

> **The key distinction.** Only **Part 2 (Symbol Detection)** and the tag-bubble detector in
> Part 3 get better by **training a model** (label → dataset → GPU train → benchmark gate →
> deploy). Parts 1, 3 (schedule/tag logic), and 4 get better by **fixing rules and re-running a
> regression corpus** — there is nothing to "train." Don't wait on a retrain to fix those.

---

## The data flow (which part touches what)

```
PDF ─▶ [1] Intake & Page Understanding ─▶ plan pages + legend + page types
           │
           ▼
       [2] Symbol Detection (YOLO)  ─────▶ detections.json  (boxes + class)
           │
           ▼
       [3] Extraction & Reconciliation ──▶ variables.json (schedule) + tags + reconcile
           │
           ▼
       [4] Delivery / App / Learning ────▶ Excel + Bluebeam PDF + QA  ──┐
                                                                        │ corrections
           ◀────────────────────────────────────────────────────────── ┘ feed Part 2/3 training
```

---

## PART 1 — Intake & Page Understanding  ("get the right pages")

**Job:** ingest the PDF, render pages, pick the *real* plan pages, classify every page
(plan / schedule / legend / details / cover), read the legend, drawing index, and title block,
flag watermark / scale / missing sheets.

**Accuracy it owns:** page-selection recall & precision (no real plan dropped; no
legend/schedule/detail page scanned for equipment) + correct legend/index/title-block read.
A miss here poisons everything downstream — a dropped plan loses *all* its equipment; a scanned
schedule sheet creates phantoms.

**Key files:**
- `takeoff_cli.py` → `render_page()`, the page-selection block, DPI/tiling config (`DPI=200`)
- `sheet_filter.py` (discipline + M-series plan selection)
- `saas/backend/page_classifier.py`
- `saas/backend/doc_verification.py` (index, watermark, completeness)
- `saas/backend/legend_reader.py`, `legend_match.py`
- `saas/backend/project_info.py`, `auto_scale.py`, `addendum_diff.py`
- `saas/backend/keynote_extractor.py` / `keynote_ocr.py`

**Known problems (current):**
- **OOM on E-size sets.** 36"×24" sheets rendered at 200 DPI (~7200×4800 px) and *held in
  memory* for tag inference → on a low-free-RAM machine the process is killed mid-run. This is
  what crashed the backend on `Mpages & RCP.pdf` (34 E-size sheets). Fix lives here: per-sheet
  DPI scaling, release rendered images after use, or stream pages.
- Page selection is fixed but fragile (two stacked filters; see CLAUDE.md "Page-filter hardening").

**Improvement workflow (NOT training):** build a tiny ground-truth file per project listing which
pages are real plans → run intake → measure page-pick recall/precision. Iterate on rules.

---

## PART 2 — Symbol Detection  ("count the equipment") — the trained model

**Job:** detect equipment symbols on the selected plan pages with YOLOv8.

**Accuracy it owns:** detection **recall & precision per class** vs Bluebeam ground truth.
This is the part whose accuracy genuinely comes **after training**.

**Key files:**
- `takeoff_cli.py` → tile (640px) → `model.predict()` → per-class NMS
- `class_thresholds.py`, `class_normalization.py`, `class_aliases.py`, `v10_class_map.py`
- `confidence_calibration.py`, `template_matcher.py`
- `models/*.pt`  (production = `hvac_yolov8s_v10.pt`; v14 regressed, not deployed)

**Known problems (current):** under-detects RTUs / roof equipment; weak on out-of-distribution
drawing styles; YOLO↔schedule class-name mismatches; v14 retrain scored worse than v10.

**Improvement workflow — THE training loop ("accuracy after training"):**
```
estimator corrects boxes in Bluebeam / the UI
   └▶ bluebeam_to_yolo.py            (corrections → YOLO labels, canonical 33-class order)
   └▶ learn_from_corrections.py      (merge into dataset vNN, dedup, prepare_training.py)
   └▶ train_v11.py                   (fine-tune v10 on GPU / Colab — imgsz 640)
   └▶ run_benchmark_suite.py         (BENCHMARK GATE: promote only if it beats v10 on held-out)
   └▶ set HVAC_MODEL=models/<new>.pt (deploy)
```
**Gate rule:** never deploy a model that doesn't beat the current one on the held-out benchmark
(v14 is the cautionary tale). Tools: `run_benchmark_suite.py`, `benchmark_samples.py`,
`confusion_matrix.py`.

---

## PART 3 — Extraction & Reconciliation  ("read the schedule, match tags, check counts")

**Job:** parse schedule tables → per-tag variables (CFM, model, size…); infer each detection's
tag; match detections ↔ schedule; reconcile counts; apply TYP/NIC; enrich attributes; emit line
items + tag report.

**Accuracy it owns:** schedule-variable correctness, **tag recall**, reconciliation correctness.

**Key files:**
- `schedule_parser.py` (text-layer tables), `saas/backend/schedule_ocr.py` (raster fallback)
- `tag_inference.py` (multi-level), `tag_matcher.py` (EasyOCR), `tag_extractor.py`
- `validation_engine.py` (reconcile), `line_items.py` (agreement gating)
- `typ_uno_nic.py`, `neck_size_waterfall*.py`, `room_grouper.py`, `room_counter.py`
- `context_enrich.py`, `data_filler.py`, `diffuser_extractor.py`, `plan_label_ocr.py`
- the tag-bubble detector `models/hvac_tag_detector_v1.pt` (used by tag_inference Level 2b)

**Known problems (current — seen today on `Mpages & RCP.pdf`):**
- Schedule parser **misdiagnoses** rich English plans as "non-English / 0 variables," then the OCR
  fallback scrapes **garbage off the wrong pages** (it OCR'd floor plans + the "NOT FOR
  CONSTRUCTION" watermark into fake tags `AND / P→ARIZ / JIXZ / G`). The real equipment data sits
  readable in the text layer. Two bugs: (a) schedule-page *location* is wrong, (b) OCR fallback
  has no "is this actually a schedule row?" guard.
- Dense E-size multi-schedule sheets not fully parsed (table-region segmentation scaffolded only).
- Tag join is exact-match; `EF1` vs `EF-1` variants not normalized.

**Improvement workflow (mostly NOT training):**
- `schedule_regression_sweep.py` — corpus-wide snapshot diff after every parser change.
- `test_parser_accuracy.py` — parser unit accuracy.
- per-tag recall via `benchmark_samples.py`.
- The **one** trained piece here is the tag-bubble detector → same train→gate→deploy loop as Part 2.

---

## PART 4 — Delivery, App & Learning Orchestration  ("show it, ship it, capture corrections")

**Job:** the web app (upload / projects / viewer / **correction UI**), output writers
(Excel / annotated PDF / Bluebeam stamps / QA report), job orchestration, and harvesting
corrections that feed Part 2/3 training.

**Accuracy/health it owns:** end-to-end benchmark score, **system stability** (no OOM crashes,
no jobs stuck in "running"), and **correction throughput** (how much training data the loop
captures — the fuel for Part 2/3).

**Key files:**
- `saas/backend/{main,config}.py`, `api/routes.py`, `api/models.py`
- `saas/backend/core/{pipeline,jobs}.py`, `worker.py`, `task_queue.py`
- `saas/backend/post_takeoff.py` (runs all post stages + manifest)
- `saas/backend/write_bluebeam_stamps.py`, `tag_report.py`, `discrepancy_report.py`,
  `quality_checks.py`, `toolbox_mapping.py`, `compare_excel.py`
- `saas/frontend/**` (viewer + correction panels + typed `lib/api.ts`)
- correction-capture endpoints + `learn_from_corrections.py` entry

**Known problems (current):**
- **In-process mode (`HVAC_INPROCESS=1`) has no memory guardrail** → a heavy job OOM-kills the
  whole backend and leaves the job frozen at `status:"running"` forever (exactly what happened
  today). Needs: out-of-process job isolation OR a memory/size pre-check + a stuck-job watchdog
  that flips dead "running" jobs to "failed."

**Improvement workflow:** stability hardening + automating the closed loop
(correction → retrain → benchmark-gate → deploy).

---

## Cross-cutting notes

1. **Two repos.** This local copy runs the web app but is an *older snapshot*. The canonical,
   more-advanced pipeline is GitHub `triunesolutions/hvac-takeoff-tool` (CLI-only, e.g. it has a
   working `ocr_table_extractor.py` and stronger detection). Before deep work on Parts 2 & 3,
   diff against the team repo so we don't re-fix what's already fixed there.
2. **Order of attack (suggested):** Part 4 stability first (so runs finish at all) → Part 1 page
   selection + memory → Part 3 schedule/tag (biggest visible accuracy bug today) → Part 2 retrain
   loop (slowest; needs corrections as fuel).
3. **Collapse to 3 parts** if preferred: merge Part 1 into Part 3 as "The Reader" (everything
   document/text), keep Part 2 "The Detector," keep Part 4 "App + Learning."

---

## Progress log

**2026-06-18 — done & verified end-to-end on `Mpages & RCP.pdf` (was crashing the backend):**
- ✅ Part 1: `LazyPageImages` in `takeoff_cli.py` — fixes the OOM (peak ~1.6 GB → ~0.2 GB).
- ✅ Part 3: `find_schedule_pages()` targets real schedule-table pages (no more 7-min scan of
  21 dense plans; no Pacific/Busy Bees regression).
- ✅ Part 3: `filter_ocr_variables()` OCR garbage guard + `--no-schedule-ocr` flag wired up.
- ✅ Part 4: `reap_stale_jobs()` startup watchdog in `core/jobs.py` + `main.py`.

Result: the PDF that crashed the backend now completes (detection 160s, 71 detections) and
reports its empty schedule honestly (it's a partial export with no schedule sheets).

**2026-06-18 (later) — Part 1 page selection rebuilt as a fused classifier:**
- ✅ `part1_page_diagnostic.py` — cross-corpus diagnostic (found the failures across 13 styles).
- ✅ `page_selector.py` — fused per-page verdict (discipline-gate + keep-unless-confident-non-drawing),
  replacing the brittle two-stacked filters. Wired into `takeoff_cli.py` (writes `_page_selection.json`).
- ✅ `benchmark_page_selection.py` — recall/precision vs title-block ground truth; **recall=1.00 on
  all 4 scoreable styles** (MPAGES, Pacific, Busy Bees, MECH Combined). Before/after: Union 888 4→10,
  MPAGES 4→9, Pacific drops the M-400 schedule phantom.
- Backend restarted with the wired-in selector.

**2026-06-19 — Part 2 detection measured + subtype/product fixes (no retrain):**
- ✅ measured: `run_benchmark_suite.py` + `rescore_aliased.py` → v10 finds ~84% of air devices
  (held-out object recall 100%); the ~31% raw recall was mostly subtype MISLABELING, not blind detection.
- ✅ Change #1 subtype-from-tag (`takeoff_cli.py`, after infer_tags; guarded to only refine to a
  more-specific air-device subtype).
- ✅ PRODUCT-from-schedule (`write_excel`): PRODUCT col uses the schedule's descriptive type for air
  devices (matches the team's "LAY-IN"/"EGGCRATE RET. GRILLE" taxonomy) — format/grouping unchanged.
- ✅ phantom thresholds (`class_thresholds.py`): DAMPER WITH TAP 0.55, OTHER MECHANICAL 0.40→0.50.
- ⚠️ v14 broken (held-out recall 0) — keep v10. Data ceiling: fine mounting subtype only recoverable
  when the schedule is descriptive (else needs a model→type lookup).

**Next (not yet done):**
- Part 2: close the correction→retrain→gate→deploy flywheel for true-miss classes (ROOFTOP UNIT,
  FIRE SMOKE DAMPER, RAIN CAP, RELIEF HOOD).
- Part 1 verify: label the 9 CAD-style sets' "uncertain" pages in `page_selection_gt.json`
  (their titles are vector — need human labels) to get the full all-styles number.
- Part 1: cap detection DPI / pre-flight memory check for very large sets (box has ~2–4 GB free).
- Part 2: the YOLO retrain loop (under-detection of RTUs / roof equipment).
- Part 3: handle sets where the real equipment schedule sheets ARE present but raster-only.

## Where your fix plan plugs in

| Your fix | Part | Type of work |
|---|---|---|
| (fill in) | | |
```
