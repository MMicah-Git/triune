# What happens when a PDF is uploaded

**Single source of truth** for the takeoff pipeline. Every stage below answers
"what should the system be doing when an estimator drops a blueprint PDF?"

**Status legend:**
- ✅ Built and working
- ⚠️ Partially built / fragile / known gap
- ❌ Not built yet
- 🎯 Required for the tool to feel "good" to estimators

---

## STAGE 0 — Receive the file ✅

When the user drops a PDF on the upload page:

1. **Validate** — file is a PDF, not corrupt, openable.
2. **Hash + dedupe** — if the same file (by content hash) was uploaded before, link to the existing job instead of re-running.
3. **Save** to `saas/data/jobs/<id>/inputs/<filename>.pdf` (streamed in 1 MB chunks).
4. **Create job record** in `jobs.json` with status `queued`.
5. **Queue** for processing — Arq if Redis is up, FastAPI BackgroundTasks otherwise.
6. **Return** `{id, status: "queued"}` immediately so the browser doesn't hang.

---

## STAGE 1 — Scan the document ✅

Before any AI work, understand the document structure:

1. **Open with PyMuPDF.** Page count, file size, any /Encrypt flag.
2. **Per-page metadata:**
   - Mediabox dimensions (the unrotated PDF page size)
   - Display dimensions (post-rotation, what the viewer sees)
   - Rotation (0 / 90 / 180 / 270) — **critical**, drove the stamping bug
   - Has text layer? (raw `page.get_text()` word count)
   - Image content fraction (vector vs raster)
3. **Detect drawing scale** per page (`auto_scale.py` exists). Flag pages with no scale or scale mismatches.

---

## STAGE 2 — Identify each page's role ⚠️🎯

Every blueprint has different kinds of pages. We need to classify them up-front.

**Page types we care about:**

| Type | What it contains | What the AI should do with it |
|---|---|---|
| **Cover / Title sheet** | Project name, owner, A/E firms, dates, sheet index | Extract metadata; do NOT run equipment detection |
| **Schedule page** | Equipment schedule tables (CU, EF, AHU, AD-GRD…) | Extract every tag + properties |
| **Legend page** | Symbol library | Build symbol → class map; do NOT detect equipment |
| **Detail / section** | Construction details, mounting drawings | Skip equipment detection (phantom source) |
| **Mechanical floor plan** | The actual rooms with equipment | Run YOLO equipment detection |
| **Roof plan** | Roof-mounted equipment (RTUs, ERVs, ECUs) | Run detection; expect outdoor units |
| **Air balance / room schedule** | Per-room CFM table | Extract for cross-check against detections |
| **Riser diagram** | Vertical piping layout | Skip equipment detection |

**Current state:**
- ⚠️ The current code runs YOLO on every page based on keywords ("MECHANICAL PLAN", "HVAC PLAN") with no skip-list for legend/schedule/details.
- 🎯 **Highest-leverage fix** (1 hour): add page-type filter to skip LEGEND/SCHEDULE/DETAILS pages. CLAUDE.md says this kills ~80% of phantom detections.

---

## STAGE 3 — Extract project metadata ⚠️

Every page that has a title block (usually all of them) should be read for:

1. **Project info** — name, number, address, owner/client
2. **A/E firms** — architect name + address, engineer name + address
3. **Dates** — issue date, revision history with dates and descriptions
4. **Sheet info** — sheet number (M001, M002…), sheet description ("MECHANICAL TITLE SHEET")
5. **People** — drawn by, checked by, approved by initials
6. **Permit info** — permit number if shown
7. **Scale callout** in title block (separate from per-drawing scale)

**Current state:**
- ✅ `extract_project_info()` in `takeoff_cli.py` works for clean title blocks (Flex 230: full extraction).
- ⚠️ Garbles on Art Vascular: date `"9/01/25"` ended up in the `project_no` field too.
- ⚠️ Sheet number (M001 etc.) doesn't have a labeled field in many title blocks — needs typography heuristic.
- ⚠️ Picks oldest date in revision history, not latest. Should pick most recent issue.

---

## STAGE 4 — Extract schedule tables ⚠️🎯

The schedule is where equipment specifications live. Every detection on the plan should be tied to a schedule row.

**For each schedule page:**

1. **Detect schedule tables** via pdfplumber's table finder + custom heuristics.
2. **Find the tag column** — looks for `MARK`, `TAG`, `UNIT TAG` header, or falls back to "row with 3+ property keywords".
3. **Detect orientation** — vertical (tags as rows) vs horizontal (tags as columns) — auto-transpose if horizontal.
4. **For each row, build a `TagVariable`:**
   - Tag string (`CU-1`, `EF-3`)
   - Schedule name (`HVAC - SPLIT DX CONDENSING UNIT SCHEDULE`)
   - Page number
   - All property columns (manufacturer, model, CFM, MCA, voltage, weight, neck size, etc.)
   - Inferred YOLO class (from prefix lookup: `CU` → CONDENSING UNIT)
5. **Expand shorthand:**
   - `CU-1 thru CU-6` → 6 separate tags sharing the row
   - `A, B, C` → 3 separate tags
   - `AC-1,2,3` → AC-1, AC-2, AC-3
6. **Normalize tags** — strip `(E)`/`(R)`/`(N)` status prefixes; reject refrigerant codes (`R-410A`); reject sheet numbers (`M102`).
7. **Special schedules** to recognize:
   - GRD schedule with **CFM-range table** for inferring neck sizes (per Deck 1 slide 7)
   - Air balance per room
   - Unit matrix (per Deck 1 slide 9)
   - Keynote schedule

**Current state:**
- ✅ Works on Flex 230, Aritzia, United Mechanical, ~25 other test projects.
- ❌ **Fails on raster-schedule PDFs** like Art Vascular: the schedule LOOKS like a table but the cell contents are graphics, not text. `variables.json` came out empty.
- 🎯 **Required fix** (1–2 days): OCR fallback. Render page → EasyOCR → reconstruct table from text bbox positions.

---

## STAGE 5 — Extract keynotes and legend ❌🎯

Per Deck 1 slides 3 and 12, equipment details are often only in keynotes — not the schedule.

**What we need:**

1. **Keynote list extraction** — find the "KEYNOTES" or "GENERAL NOTES" block, extract every numbered note.
2. **Keynote reference on plan** — find every `<1>`, `(2)`, etc. callout on the plan pages.
3. **Cross-reference** — for each detection, find nearest keynote callout, link to keynote text.
4. **Flag missing references** — keynotes mentioned in the list but not placed on the plan (Deck 1 slide 12).
5. **Legend symbol extraction** — symbol library page that maps icons to equipment classes.

**Current state:**
- ❌ Not built. The Deck 1 challenges sit unaddressed.
- Would close two of the biggest manual-verification time sinks the team flagged.

---

## STAGE 6 — Cross-discipline lookup ❌

Per Deck 1 slide 4, equipment tags are sometimes on **plumbing or piping plans**, not the mechanical sheets.

**What we need:**

1. Run text/keyword scan on every page regardless of type.
2. For any tag that appears in the schedule but NOT in a mechanical-page detection, search non-mechanical pages.
3. Surface "tag X is referenced on PL-2 but no mechanical detection found" as a flag.

**Current state:** ❌ Not built.

---

## STAGE 7 — Equipment detection (YOLO) ✅

For each page identified as a mechanical/roof plan:

1. **Render** at 200 DPI via PyMuPDF.
2. **Tile** into 640×640 windows with overlap (handles large sheets).
3. **Run YOLOv8** (`models/hvac_yolov8s_v10.pt`) — 35 classes including diffusers, dampers, fans, condensing units, etc.
4. **Tile-NMS** to merge overlapping detections from adjacent tiles.
5. **Page-level NMS** to suppress duplicate boxes on the same physical equipment.
6. **Output:** per-page list of `{cls, conf, x1,y1,x2,y2}` in pixel coords at the rendered DPI.

**Current state:**
- ✅ Works. v10 hits ~44% median full-recall on the 35-project benchmark.
- ⚠️ Phantom detections on legend/schedule pages (Stage 2 fix would prevent these).
- ⚠️ Page-level NMS exists per-tile but cross-tile gaps remain (CLAUDE.md "open follow-ups" #4).

---

## STAGE 8 — Tag-bubble detection ✅

Separately from equipment detection, find every small **tag bubble** (`CU-1`, `A1` text in a circle/oval next to a symbol).

1. Run `models/hvac_tag_detector_v1.pt` (separate YOLO model trained on 26K tag bubbles).
2. For each bubble, OCR the tight crop with EasyOCR.
3. Match OCR token against the valid tag list from Stage 4.

**Current state:** ✅ Built. Used as Level 2b in tag inference.

---

## STAGE 9 — Tag assignment per detection ✅

For each detected equipment box, figure out which tag it represents.

**Three-level system, applied in order:**

1. **Level 1 — Direct mapping:** if the schedule has exactly 1 tag for this YOLO class, auto-assign every detection of that class to that tag. Works for projects like Flex 230 where there's one of each.
2. **Level 2a — Fingerprint match:** for multi-tag classes (CU-1…CU-6), check the PDF text near each detection for distinctive tokens (CFM, model #) from the schedule, score (detection, tag) pairs, greedy 1:1 assign.
3. **Level 2b — Bubble OCR:** for any still-untagged detection, find the nearest tag bubble, read it, match to a valid schedule tag for that class. No 1:1 cap (tag like `A1` repeats many times).
4. **Level 3 — Fallback:** mark as untagged. Track in `tag_method: "none"`.

**Current state:** ✅ Built.

---

## STAGE 10 — Apply your team's tagging rules (Deck 2) ✅

These are the context-aware rules from the "UNIQUE FILE TAGGING" deck:

| Rule | Trigger | Action |
|---|---|---|
| **FSD context** | Fire Smoke Damper near AD-GRD? | Tag = `FSD-OP`, type = `OUT OF PARTITION` |
| | Fire Smoke Damper alone in duct? | Tag = `FSD`, type = `INLINE DUCTED` |
| **CRD detection** | "CRD" text near AD-GRD detection? | Set damper_type = `CRD` |
| **Linear diffuser merge** | N colinear linear diffusers? | Merge into one with summed face length |
| **TYP/TYPICAL marking** | Detection on a "TYP" symbol? | Replicate to all similar-placement instances |
| **Slot width from model name** | Model name like "AS22O"? | Derive slot width = 2" |
| **Curved slot diffuser** | Curved AD-LINEAR? | Compute chord+rise → arc length |

**Current state:**
- ✅ FSD context, CRD detection, linear merging implemented in `context_enrich.py`.
- ❌ TYP/TYPICAL replication: not built.
- ❌ Slot-width-from-model: not built.
- ❌ Curved diffuser arc calc: not built.

---

## STAGE 11 — Fill in missing data ❌🎯

Per Deck 1 slides 5, 6, 7, the estimator has to manually figure out:
- Duct sizes (when not labeled)
- Damper sizes (derive from duct size)
- GRD neck sizes (lookup from CFM-range table)
- Slot widths (derive from model name)

**What we need:**

1. **Duct vector parsing** — read the actual duct line geometry, measure width.
2. **Damper-from-duct inference** — find the duct each damper sits in, use that width.
3. **Neck-size-from-CFM** — read schedule's CFM range table, look up each tag's CFM, assign neck size.
4. **Slot-width-from-model** — regex-match model names for size indicators.

**Current state:** ❌ Not built. Each estimator does this manually today.

---

## STAGE 12 — Quality + consistency checks ❌

Before producing outputs, run sanity checks the estimator usually does:

1. **Tag mismatch flag** — tags on plans that aren't in the schedule, or vice versa (Deck 1 slide 8).
2. **Unit name mismatch** — between enlarged plans and overall floor plan (Deck 1 slide 10).
3. **Quantity reasonableness** — too many of one equipment type vs expected for project size?
4. **Scale consistency** — same scale used across drawings that should match.

**Current state:** ❌ Not built.

---

## STAGE 13 — Produce outputs ✅⚠️

For a successful run, generate:

| Output | What it is | Status |
|---|---|---|
| `_takeoff.xlsx` | Bill of Materials in your Bluebeam-compatible format | ✅ |
| `_annotated.pdf` | Original PDF with AI boxes drawn on it (raster) | ✅ |
| `_bluebeam_stamped.pdf` | Original PDF with **native Bluebeam stamps** using NSW-ToolBox subjects/colors | ✅ NEW |
| `_detections.json` | Raw per-page detection list | ✅ |
| `_variables.json` | All TagVariable rows from schedules | ✅ (empty if no schedule readable) |
| `_project_info.json` | Title-block metadata | ⚠️ |
| **Per-room counts** | "Conference 302: 4 diffusers, 1 damper" | ❌ |
| **Discrepancy report** | Missing tags, scale issues, phantom warnings | ❌ |

---

## STAGE 14 — Notify and hand off ⚠️

When the job finishes:

1. Mark job as `done` in `jobs.json`.
2. Frontend polling picks it up — download links appear on the project page.
3. (Future) Email or push notification to the estimator.

The estimator then:

1. Downloads the **Bluebeam-stamped PDF**.
2. Opens in Bluebeam Revu.
3. Markups List shows all AI stamps grouped by subject.
4. Estimator deletes wrong ones, stamps missed ones using their NSW-ToolBox.
5. Saves and uploads as a "correction" via the project page.

---

## STAGE 15 — Learn from corrections ✅

When the estimator uploads a corrected PDF (`POST /api/jobs/{id}/correction`):

1. **Extract polygons** synchronously — bluebeam_to_yolo reads every stamp's Subject + rect.
2. **Convert to YOLO labels** — class id from Subject, bbox normalized.
3. **Save** to `saas/data/corrections/<job>/yolo/`.
4. **Append** to `saas/data/training_queue.jsonl`.
5. **Return** counts to user — green confirmation box.
6. **Later (manual):** `python learn_from_corrections.py` bundles queue + base dataset → Kaggle train → benchmark → swap model if F1 improves ≥3%.

**Current state:** ✅ Built, never exercised with a real correction yet.

---

## The honest scorecard

| Stage | Built? | Worth fixing first? |
|---|---|---|
| 0. Receive file | ✅ | — |
| 1. Document scan | ✅ | — |
| **2. Page-type detection** | ⚠️ | **🎯 YES — 1 hour, kills 80% of phantoms** |
| 3. Project metadata | ⚠️ | nice-to-have |
| **4. Schedule table extraction** | ⚠️ | **🎯 YES — OCR fallback for raster schedules** |
| 5. Keynotes + legend | ❌ | high value, 1 week |
| 6. Cross-discipline | ❌ | medium value, 2 days |
| 7. Equipment detection | ✅ | (improves via Stage 15 loop) |
| 8. Tag-bubble detection | ✅ | — |
| 9. Tag assignment | ✅ | (improves with Stage 4 fix) |
| 10. Deck 2 rules | ⚠️ | 3 rules done, 3 left |
| **11. Missing-data fill** | ❌ | **🎯 medium-term moat builder** |
| 12. Quality checks | ❌ | nice-to-have |
| 13. Outputs | ✅ | per-room counts pending |
| 14. Notify | ⚠️ | — |
| 15. Learn from corrections | ✅ | needs first real submission |

---

## The two things to fix this week

1. **Add legend/schedule/details page filter (Stage 2)** — 1 hour, drops phantom rate from ~24% to ~5%.
2. **Add OCR fallback for raster schedules (Stage 4)** — 1–2 days, unlocks every CAD-export PDF like Art Vascular.

After those two: the system goes from "promising but rough" to "estimator's daily-use tool."
