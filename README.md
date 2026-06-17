# HVAC Takeoff Tool

An AI-assisted estimating tool that performs **HVAC takeoffs directly from construction-drawing PDFs**. It detects air-distribution devices and equipment on the plans, matches each to its equipment schedule, reconciles the counts, and produces a quantified takeoff plus annotated and Bluebeam-ready drawings. It learns continuously from estimator corrections.

> **Status:** working end-to-end pipeline + web app. Detection/extraction accuracy is still maturing and improves via the self-learning loop (see [Limitations](#limitations--roadmap)). Not a finished estimator replacement.

---

## Table of contents
- [What it does](#what-it-does)
- [The workflow](#the-workflow)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Quick start](#quick-start)
- [Program reference](#program-reference)
  - [Takeoff pipeline core (repo root)](#1-takeoff-pipeline-core-repo-root)
  - [Post-takeoff stages (saas/backend)](#2-post-takeoff-stages-saasbackend)
  - [Web service (saas/backend)](#3-web-service-saasbackend)
  - [Frontend (saas/frontend)](#4-frontend-saasfrontend)
  - [Class taxonomy](#5-class-taxonomy)
  - [ML training & self-learning loop](#6-ml-training--self-learning-loop)
  - [Benchmarking, tests & analysis](#7-benchmarking-tests--analysis)
  - [Notebooks](#8-notebooks)
- [Outputs produced per job](#outputs-produced-per-job)
- [Configuration](#configuration)
- [Models](#models)
- [Limitations & roadmap](#limitations--roadmap)

---

## What it does

HVAC estimators manually count every diffuser, grille, damper, fan, and unit across dozens of drawing sheets, look each tag up in the schedule, record its attributes (CFM, neck size, model, manufacturer), and mark it off. This tool automates that:

1. **Ingests** a mechanical drawing-set PDF (plans + schedules + legend + notes).
2. **Detects** equipment symbols on the plans with a YOLOv8 computer-vision model.
3. **Tags** each detection (e.g. `A`, `EF-1`) and looks it up in the equipment schedule.
4. **Reconciles** detections against the schedule and flags discrepancies.
5. **Outputs** an Excel takeoff, an annotated PDF, and a Bluebeam-ready stamped PDF.
6. **Learns** from estimator corrections to improve the model over time.

---

## The workflow

The tool mirrors a manual takeoff in two steps.

### Step 1 — Document gathering & verification
- Classify drawing disciplines (Mechanical / Architectural / Structural) despite inconsistent sheet naming (`M-001`, `MECH-101`, `HV-1`, …).
- Read the cover-sheet drawing index and check the uploaded set for completeness.
- Detect `NOT FOR CONSTRUCTION` watermarks.
- Classify each page (plan / schedule / legend / details / cover).
- Support addenda diffing (old vs new revision).

### Step 2 — Legend → Plan → Tag → Schedule → Record → Mark
- Read the legend into a symbol dictionary.
- Detect symbols on the plans (YOLOv8, ~33 equipment classes).
- Infer each symbol's tag (multi-level: direct map → text-layer callouts → fingerprint → bubble-OCR).
- Parse the equipment schedules and capture attributes (CFM, neck size, model, manufacturer, mounting).
- Reconcile schedule rows vs detections; flag orphans and missing items.
- Apply plan-note semantics: **NIC** (not in contract → excluded) and **(TYP OF N)** multipliers.
- Mark counted items on an annotated + Bluebeam-ready stamped PDF.

---

## Architecture

```
                        ┌─────────────────────────┐
   Browser (estimator)  │   Next.js 14 frontend    │   saas/frontend  (port 3000)
                        │  upload / projects / view │
                        └────────────┬─────────────┘
                                     │ HTTP (typed client: lib/api.ts)
                        ┌────────────▼─────────────┐
                        │   FastAPI backend         │   saas/backend   (port 8000)
                        │   api/routes.py           │
                        │   core/jobs.py (job store)│
                        │   core/pipeline.py        │
                        └────────────┬─────────────┘
                                     │ calls
              ┌──────────────────────▼───────────────────────┐
              │  Takeoff pipeline (repo root)                 │
              │  takeoff_cli.py: render → YOLO detect → tag   │
              │  → schedule parse → reconcile → Excel/PDF     │
              └──────────────────────┬───────────────────────┘
                                     │ then
              ┌──────────────────────▼───────────────────────┐
              │  Post-takeoff stages (saas/backend)           │
              │  post_takeoff.py orchestrates: page classify, │
              │  schedule OCR, keynotes, cross-discipline,     │
              │  TYP/NIC, neck-size, QA, Bluebeam stamps,      │
              │  room counts, discrepancy + tag reports        │
              └───────────────────────────────────────────────┘

   Self-learning loop:  estimator corrects the Bluebeam PDF → uploads it →
   bluebeam_to_yolo extracts labels → learn_from_corrections merges into a new
   dataset → train_v11 retrains on GPU → benchmark gate → deploy new model.
```

**Stack:** FastAPI (Python) · Next.js 14 + TypeScript · YOLOv8 (Ultralytics) · PyMuPDF (PDF) · EasyOCR + OpenCV (raster fallback) · openpyxl (Excel). File-based job store (designed to move to Postgres/S3).

---

## Repository layout

```
.
├── takeoff_cli.py              Main takeoff engine (CLI)
├── schedule_parser.py          Schedule + legend table parser
├── tag_inference.py            Multi-level tag assignment
├── *.py                        Pipeline modules, training, benchmarks (see below)
├── models/                     YOLO weights (.pt)
├── templates/                  Legend symbol templates (needed for detection)
├── ground_truth/, *.jsonl      Training labels
├── docs/                       Result CSVs + design notes
├── *.ipynb                     Colab / Kaggle training notebooks
└── saas/
    ├── backend/                FastAPI service + post-takeoff stages
    │   ├── main.py, config.py
    │   ├── api/                routes + schemas
    │   ├── core/               jobs + pipeline bridge
    │   └── *.py                post-takeoff stage modules
    ├── frontend/               Next.js app
    │   ├── app/                pages
    │   ├── components/         viewer + panels
    │   └── lib/api.ts          typed API client
    ├── data/                   runtime artifacts (gitignored: uploads, outputs, corrections)
    └── README.md               SaaS local-dev guide
```

> **Not in the repo (gitignored):** `saas/data/` (customer drawings + job outputs), `yolo_dataset_v*/` (training datasets), `colab_bundle/`, `kaggle_bundle/`, `runs/` (training runs), logs.

---

## Quick start

### Backend (FastAPI, port 8000)
```bash
cd saas/backend
pip install -r requirements.txt          # one-time
HVAC_INPROCESS=1 python -m uvicorn main:app --port 8000
```
- `HVAC_INPROCESS=1` keeps the YOLO model warm in-process (no Redis needed). API docs at <http://localhost:8000/docs>.

### Frontend (Next.js, port 3000)
```bash
cd saas/frontend
npm install                              # one-time
npm run build && npm start               # production (stable)
# or: npm run dev                        # hot reload (dev)
```
Open <http://localhost:3000>.

### Run a takeoff from the CLI (no web app)
```bash
python takeoff_cli.py "path/to/drawings.pdf"
# Outputs land next to the PDF in <stem>_takeoff/
```

See [`saas/README.md`](saas/README.md) for the full endpoint list and self-learning details.

---

## Program reference

### 1. Takeoff pipeline core (repo root)

| File | What it does |
|---|---|
| `takeoff_cli.py` | **Main engine.** Renders pages, tiles them (640px @ 200 DPI), runs YOLO, applies per-class confidence thresholds + NMS, infers tags, parses schedules, reconciles, writes Excel + annotated PDF. Has an OCR schedule fallback. |
| `takeoff.py` | Earlier prototype (kept: imported by `label_tag_bubbles.py`). |
| `schedule_parser.py` | Parses equipment-schedule + legend tables (pdfplumber text layer) → tags + attributes. |
| `tag_inference.py` | Assigns a tag to each detection — levels: direct class→tag map, text-layer hex callouts, value fingerprint, bubble-detector OCR, windowed OCR, fallback. |
| `tag_extractor.py` | Lower-level tag extraction helpers. |
| `tag_matcher.py` | Schedule-guided tag matching. |
| `sheet_filter.py` | Classifies sheet discipline (M/A/S) from the title-block sheet number; selects M-series plan pages. |
| `auto_scale.py` | Detects drawing scale per page. |
| `addendum_diff.py` | Diffs two PDF revisions (IoU-matches detections → added/removed/moved/relabeled). |
| `validation_engine.py` | Reconciliation — schedule rows vs detections (match / under / over, orphan tags). |
| `line_items.py` | Unified, evidence-carrying output contract + agreement gating (confirmed / needs-review / flagged). |
| `room_grouper.py` | Groups detections into rooms ("automatic room search"). |

### 2. Post-takeoff stages (saas/backend)

Orchestrated by **`post_takeoff.py`** after the core takeoff produces `detections.json` + `variables.json`:

| File | Stage / role |
|---|---|
| `post_takeoff.py` | Runs all stages below, writes the manifest. |
| `page_classifier.py` | Classify each page (plan / schedule / legend / details / cover). |
| `project_info.py` | Title-block / project info extraction. |
| `schedule_ocr.py` | OCR fallback for non-text (raster / broken-font) schedules — multi-block parsing + OpenCV table-region segmentation. |
| `keynote_extractor.py` / `keynote_ocr.py` | Extract keynotes (text layer + OCR fallback) and link to detections. |
| `cross_discipline.py` | Find orphan tags across disciplines. |
| `typ_uno_nic.py` | Plan-note semantics: **NIC** exclusion + **(TYP OF N)** multipliers. |
| `data_filler.py` | Fill missing neck/slot data. |
| `neck_size_waterfall.py` / `_runner.py` | Per-detection neck-size extraction (multi-source waterfall). |
| `diffuser_extractor.py` | Per-instance diffuser/grille plan-label extraction. |
| `plan_label_ocr.py` | OCR-based plan-label extraction (raster plans). |
| `quality_checks.py` | QA warnings by severity. |
| `room_counter.py` | Per-room equipment counts. |
| `discrepancy_report.py` | Human-readable QA report (Markdown + JSON). |
| `tag_report.py` | Tag-by-tag breakdown (JSON / Markdown / Excel) joining plan labels + YOLO + schedule. |
| `context_enrich.py` | Deck-2 tagging rules: fire/smoke-damper context, ceiling-radiation damper, linear-diffuser merging. |
| `curved_diffuser.py` / `mitered_corners.py` | Special geometry handling for linear/curved diffusers. |
| `write_bluebeam_stamps.py` | Writes real Bluebeam **PolygonCount** stamps onto a PDF (appears in Bluebeam's Markups List). |
| `toolbox_mapping.py` | Maps AI classes → NSW ToolBox subjects + colors (with a fallback so nothing is dropped). |
| `doc_verification.py` | Step-1 document-set verification (sheet index, watermark). |
| `legend_reader.py` / `legend_match.py` / `template_matcher.py` | Legend symbol dictionary + template matching. |
| `confidence_calibration.py` | Calibrates raw detection/neck confidences into honest numbers. |
| `compare_excel.py` | Compare two takeoff Excels (ours vs team). |
| `backfill_typ_nic.py` | Re-applies TYP/NIC counting to already-processed jobs. |

### 3. Web service (saas/backend)

| File | Role |
|---|---|
| `main.py` | FastAPI entrypoint. |
| `config.py` | All paths/constants + env overrides. |
| `api/routes.py` | HTTP endpoints: upload, jobs, file download, page render, corrections, legend, verification, stamp backfill. |
| `api/models.py` | Pydantic request/response schemas. |
| `core/jobs.py` | File-based job tracker (`jobs.json`, atomic writes). |
| `core/pipeline.py` | Bridges the CLI takeoff tools as callable pipeline stages + runs post-takeoff. |
| `worker.py` | Arq worker — runs jobs with the model warm in memory (when Redis is available). |
| `task_queue.py` | Helper to enqueue jobs to Arq/Redis. |

**Key endpoints** (full list in `saas/README.md`): `POST /api/jobs/takeoff`, `/addendum`, `/scale`; `GET /api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/file?role=…`; `POST /api/jobs/{id}/correction`, `/correction_boxes`, `/stamp`, `/index_pdf`.

### 4. Frontend (saas/frontend)

| File | Role |
|---|---|
| `app/page.tsx` | Landing page. |
| `app/upload/page.tsx` | Drag-drop upload. |
| `app/projects/page.tsx` | Job/project list. |
| `app/projects/[id]/page.tsx` | Job detail: outputs, downloads, viewer, reports. |
| `components/BlueprintViewer.tsx` | Plan viewer with detection overlay. |
| `components/LegendPanel.tsx` | Legend symbol dictionary. |
| `components/PagesPanel.tsx` | All-pages gallery / document scan. |
| `components/SchedulePanel.tsx` | Schedule display. |
| `components/UploadDropzone.tsx` | Upload widget. |
| `lib/api.ts` | Typed API client. |

### 5. Class taxonomy

| File | Role |
|---|---|
| `v10_class_map.py` | **Canonical 33-class list** (`V10_CLASSES`, exact model-head order) + `map_subject()` (Bluebeam subject → canonical class). The single source of truth for training labels. |
| `class_normalization.py` | Reduced taxonomy for the inference/reconciliation layer. |
| `class_aliases.py` | Merges duplicate/equivalent class names. |
| `class_thresholds.py` | Per-class YOLO confidence thresholds (raw-class lookup, fallback to normalized). |

### 6. ML training & self-learning loop

The loop: **estimator corrects → upload → extract → merge → retrain → benchmark → deploy.**

| File | Role |
|---|---|
| `bluebeam_to_yolo.py` | Convert Bluebeam-marked PDFs → YOLO labels, mapped to **canonical V10 indices**. |
| `learn_from_corrections.py` | Read `training_queue.jsonl`, **remap each correction's labels to canonical order**, merge into a new dataset version (base = `yolo_dataset_v14`), dedup, run `prepare_training`. |
| `build_v14_dataset.py` | Build the tiled 33-class dataset (640px tiles). |
| `build_v11_dataset.py` | Older builder (kept: imported by `build_v14_dataset.py`). |
| `prepare_training.py` | Emit `data.yaml` + train/val split. |
| `train_v11.py` | **Fine-tune v10 → new model.** imgsz 640 (matches tiles + inference), tuned augmentation, runtime-portable `data.yaml`/list paths, run-name-based weight promotion. |
| `train_yolo.py` | Alternate/legacy training entrypoint. |
| `make_colab_bundle.py` | Zip the latest dataset + v10 base + `train_v11.py` for Colab. |
| `package_for_kaggle.py` | Package the dataset for Kaggle GPU training. |
| `label_tag_bubbles.py` / `label_tag_bubbles_ocr.py` | Label tag bubbles for the tag-detector dataset. |
| `export_to_label_studio.py` / `import_from_label_studio.py` | Label Studio review round-trip. |

### 7. Benchmarking, tests & analysis

| File | Role |
|---|---|
| `run_benchmark_suite.py` | v10-vs-vNN count-based benchmark over `benchmark_manifest.json` (held-out + in-sample); prints a promote/keep verdict. |
| `benchmark_v10_vs_v11.py` | IoU/count comparison of two models against Bluebeam-truth PDFs. |
| `benchmark_samples.py` | Run the full `takeoff_cli` on sample projects and score our Excel vs the team's (reads the tagged sheet; reports product + tag-only recall). |
| `benchmark.py` | Older single-model benchmark (kept: imported by `confusion_matrix.py`). |
| `schedule_regression_sweep.py` | Corpus-wide schedule-parser regression test. |
| `confusion_matrix.py` | Detection confusion-matrix analysis. |
| `test_parser_accuracy.py` | Schedule-parser accuracy test. |
| `verify_dataset.py` / `verify_pipeline.py` | Sanity-check a dataset / the pipeline. |

### 8. Notebooks

| File | Role |
|---|---|
| `colab_train_v16.ipynb` | **Current** Colab training notebook (v16 = v14 base + folded corrections). |
| `colab_train.ipynb`, `kaggle_train*.ipynb` | Earlier Colab/Kaggle training notebooks. |
| `colab_label_bubbles.ipynb` | Tag-bubble labeling notebook. |

---

## Outputs produced per job

`<stem>_takeoff.xlsx` (Excel takeoff) · `<stem>_annotated.pdf` (AI boxes, any viewer) · `<stem>_bluebeam_stamped.pdf` (count stamps for Bluebeam's Markups List) · `_detections.json` · `_variables.json` (schedule) · `_reconciliation.json/.txt` · `_tag_report.json/.md/.xlsx` · `_qa.json` + `_qa_report.md` · `_keynotes.json` · `_project_info_v2.json` · `_typ_uno_nic.json` · `_room_counts.json` · `_post_manifest.json`.

> **Annotated vs Bluebeam:** the annotated PDF is plain rectangles for quick *viewing* in any PDF reader; the Bluebeam-stamped PDF carries real count stamps you *work with and correct* in Bluebeam — and corrections feed the learning loop.

---

## Configuration

Environment overrides (see `saas/backend/config.py`):

| Variable | Default | Notes |
|---|---|---|
| `HVAC_INPROCESS` | unset | `1` = run the model in-process (warm, no Redis). |
| `HVAC_MODEL` | `models/hvac_yolov8s_v10.pt` | YOLO weights to use (set this to deploy a new model). |
| `HVAC_DATA_DIR` | `saas/data` | Where uploads + outputs live. |
| `HVAC_CORS_ORIGINS` | `localhost:3000` | Allowed frontend origins. |
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | Frontend → backend URL (baked at build time). |

---

## Models

`models/` (tracked):
- `hvac_yolov8s_v10.pt` — **production** detection model (33 classes).
- `hvac_yolov8s_v14.pt` — 33-class retrain (not deployed; regressed on held-out — see roadmap).
- `hvac_yolov8s_v9.pt` — earlier baseline.
- `hvac_tag_detector_v1.pt` — tag-bubble detector (used by tag inference).

Deploy a model by pointing `HVAC_MODEL` at its file (after passing the benchmark gate).

---

## Limitations & roadmap

**Current limits**
- Detection recall varies by drawing style; weak on out-of-distribution styles and PDFs with broken/non-extractable font encoding.
- Dense E-size multi-schedule sheets aren't fully parsed by OCR yet (table-region segmentation is scaffolded, not finished).
- Tag↔schedule matching works on exact tags; dash/format variants (`EF1` vs `EF-1`) aren't yet normalized at the report join.

**Roadmap**
- Retrain with the corrected taxonomy + tuned config (v16) and pass the benchmark gate before deploying.
- Finish OCR table-region segmentation for dense sheets.
- Automate the correction → retrain → benchmark-gate → deploy loop.
- Auth, billing, multi-tenancy, Postgres + S3, production deploy config.

---

*Internal tool for HVAC estimating. See `CLAUDE.md`, `PRD.md`, and `WHAT_WE_ARE_BUILDING.md` for deeper design notes.*
