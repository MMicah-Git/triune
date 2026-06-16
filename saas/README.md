# HVAC Takeoff SaaS — local dev

A FastAPI backend + Next.js 14 frontend wrapping the existing
`takeoff_cli.py` / `addendum_diff.py` / `auto_scale.py` pipelines.

```
saas/
├── backend/                    FastAPI service (port 8000)
│   ├── main.py
│   ├── config.py
│   ├── api/   (routes, models)
│   ├── core/  (jobs, pipeline)
│   └── requirements.txt
├── frontend/                   Next.js app (port 3000)
│   ├── app/
│   │   ├── page.tsx                  landing
│   │   ├── upload/page.tsx           drag-drop upload
│   │   ├── projects/page.tsx         project list
│   │   └── projects/[id]/page.tsx    job detail + downloads
│   ├── components/
│   ├── lib/api.ts                    typed API client
│   └── package.json
└── data/                       runtime artifacts (jobs.json, uploads, outputs)
```

## Run it locally

### 1. Backend (FastAPI)

```bash
# from repo root
cd saas/backend
pip install -r requirements.txt          # one-time
python -m uvicorn main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs>

### 2. Frontend (Next.js)

```bash
# from repo root
cd saas/frontend
npm install                              # one-time
npm run dev
```

Open <http://localhost:3000>.

### 3. Warm-model worker (optional but recommended)

The API enqueues jobs to Redis via Arq. A long-lived worker process picks
them up with the YOLO model already in memory — eliminates the ~5 s cold
start every job otherwise pays.

```bash
# Prerequisite: Redis running on localhost:6379
#   • Windows:  install Memurai (https://www.memurai.com/) — Redis-compatible
#   • Mac:      brew install redis && brew services start redis
#   • Linux:    apt install redis-server
#   • Docker:   docker run -p 6379:6379 redis

# In a separate terminal:
cd saas/backend
arq worker.WorkerSettings
```

When the worker starts, the YOLO model is preloaded; subsequent jobs run
in ~30 s instead of ~3 min.

**If Redis isn't running**, the FastAPI service automatically falls back
to in-process `BackgroundTasks`, so the system still works — just with
the slower cold start per job.

Verify Redis health from your shell:
```bash
curl http://localhost:8000/health
# {"ok":true,"version":"0.1.0"}
```

## Endpoints

| Method | Path | Body | What it does |
|---|---|---|---|
| POST | `/api/jobs/takeoff` | `pdf` (multipart) | Full takeoff pipeline → Excel + annotated PDF |
| POST | `/api/jobs/addendum` | `old`, `new` (multipart) | Diff two PDF versions |
| POST | `/api/jobs/scale` | `pdf` (multipart) | Auto-detect drawing scale per page |
| GET  | `/api/jobs` | — | List all jobs |
| GET  | `/api/jobs/{id}` | — | Job status + outputs + live log tail |
| GET  | `/api/jobs/{id}/file?role=excel` | — | Download a single output by role |

## Architecture notes

- **Job tracking**: file-based `jobs.json` in `saas/data/` (single JSON, atomic
  writes, thread-local lock). Swap for Postgres in v2.
- **Workers**: Arq + Redis (when available) with FastAPI `BackgroundTasks`
  as a transparent fallback. The Arq worker (`worker.py`) preloads the
  YOLO model once at startup and reuses it across jobs.
- **Pipeline bridge**: `core/pipeline.py` supports both subprocess (default
  for BackgroundTasks) and in-process execution (used by the Arq worker,
  triggered by `HVAC_INPROCESS=1`). In-process mode keeps the cached YOLO
  in memory across jobs.

## Environment overrides

| Variable | Default | Notes |
|---|---|---|
| `HVAC_DATA_DIR` | `<repo>/saas/data` | Where uploads + outputs live |
| `HVAC_MODEL` | `<repo>/models/hvac_yolov8s_v10.pt` | YOLO weights to use |
| `HVAC_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Frontend origins |
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000` | Frontend → backend URL |

## Self-learning loop

The model gets better every time an estimator corrects its output. Here's
the cycle:

```
   1. Customer uploads project.pdf
        ↓
   2. AI generates Excel + annotated.pdf
        ↓
   3. Estimator opens the original drawing in Bluebeam, stamps every
      piece of equipment (their normal takeoff workflow)
        ↓
   4. Estimator uploads the corrected, marked-up PDF via
      /projects/{id} → "Submit correction" button
        ↓
   5. Backend extracts the polygons (bluebeam_to_yolo.process_project)
      and appends a record to saas/data/training_queue.jsonl
        ↓
   6. [Manual] You run `python learn_from_corrections.py` from the
      repo root when ready to retrain
        ↓
   7. Script merges base dataset + all queued corrections into
      yolo_dataset_v<NEXT>/ + emits a Kaggle-ready training bundle
        ↓
   8. Upload the bundle to Kaggle, train for ~60 epochs on T4 GPU
        ↓
   9. Download new model weights → models/hvac_yolov8s_v<NEXT>.pt
        ↓
  10. Benchmark vs current production model on a holdout PDF:
        python benchmark_v10_vs_v11.py --pdf <holdout> --truth <markup>
        ↓
  11. If new model's F1 beats current by ≥ 3%, deploy it
      (set HVAC_MODEL env var or edit config.DEFAULT_MODEL)
        ↓
  Cycle repeats — the more corrections submitted, the smarter the
  next model.
```

**Endpoints involved:**
- `POST /api/jobs/{id}/correction` — accepts a Bluebeam-marked corrected PDF for a completed takeoff job. Returns # polygons extracted + class breakdown.

**Files involved:**
- `saas/data/corrections/<job_id>/` — per-job correction storage (original PDF + extracted YOLO labels)
- `saas/data/training_queue.jsonl` — global queue, one line per accepted correction
- `learn_from_corrections.py` — retraining CLI, manual trigger

**Safety rails:**
- Never auto-deploys a new model — always requires manual benchmark + manual swap
- Original training set is never modified; corrections are *added* to a new dataset version
- Each version (v12, v13, …) is kept on disk so you can roll back

## What's not built yet

- Auth (will integrate Clerk or Auth0)
- Billing (Stripe metered)
- Multi-tenancy / per-org data isolation
- Postgres + S3 storage
- Worker queue (Arq/RQ + Redis)
- Production deploy config (Dockerfile, Fly.io / Render / Vercel)
- Cooper-style document chat (RAG with LLM)
- Interactive QuickDraw mode (Canaveral-style hover-and-click UI)
