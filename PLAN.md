# HVAC AI Takeoff — Plan & Direction

**Version:** 1.0 · **Date:** 2026-06-04
**Status:** Working document. Use as input to `/plan-eng-review`, `/plan-ceo-review`, `/codex consult`.

---

## 1. The contract with the estimator

> Upload an HVAC PDF. Get back an Excel takeoff + a Bluebeam-marked PDF.
> Every output row carries a confidence score and source. Never a silent guess.
> Estimator corrections feed the next model version.

Everything we build serves this contract. Anything that doesn't is noise.

---

## 2. Current state (honest)

### What works

- Page classification on PDFs with M-series sheet numbers (M001/M101/M201)
- Schedule extraction from text-layer schedules → `variables.json` with tag/manufacturer/model/module
- YOLO v10 equipment detection on plan pages (~33 classes)
- Tag bubble detector + OCR for tag inference (Level 2b')
- Bluebeam stamp writer with rotation-aware coordinates
- Correction submission pipeline (queue, not yet exercised with real corrections)

### What doesn't work reliably

- **Per-project legend reading** — system never learns symbols from THIS drawing's legend; relies on YOLO's pre-training
- **Neck size extraction** — only via existing diffuser_extractor (limited coverage)
- **Confidence scoring** — implicit, not surfaced to estimator
- **Calibration** — no way to know if predicted accuracy matches actual
- **Page filter** — has known off-by-one and over-aggressive issues; needs hardening

### Real measured accuracy

| PDF | Method | Result |
|---|---|---|
| Busy Bees (CAD-export, text-layer) | tag_report.py per-instance | 97% match on plan instances (113/116) |
| Art Vascular (raster) | full pipeline | ~10% — no extractable schedule |
| Pacific Palisades | full pipeline | only 5 detections (under-detection) |
| HLPUSD | full pipeline | 98 detections, only 2 stamps in output (page-filter bug) |

**Pattern:** accuracy is hostage to whether the PDF has text-layer schedules + plan labels. Drawings that don't match training data fail badly.

---

## 3. The chosen architectural direction

**Direction 1+ — YOLO (existing) + neck-size waterfall enrichment + calibrated confidence scoring.**

Decided 2026-06-04 via /plan-eng-review. Direction 2 (per-project legend reading)
deferred — too speculative to commit timeline before waterfall proves out. Revisit
after Phase 3 validation: if waterfall accuracy is bottlenecked by tagging errors,
escalate to legend reader.

Rejected alternatives:
- **Direction 2 (full hybrid w/ legend reader):** Adds 3-4 weeks. Tooling unproven on raster legends. Defer.
- **Direction 3 (replace YOLO entirely):** Throws away working capability for symbols YOLO already knows.

The chosen direction keeps YOLO as the workhorse + adds per-instance neck-size
extraction (waterfall) + makes confidence visible to estimators.

---

## 4. The neck-size waterfall (build first)

5-level cascade. First level that returns a value wins. Each level emits `(neck_size, confidence, source)`.

| Level | What it does | When it fires | Confidence ceiling |
|---|---|---|---|
| 1 | Text-layer tag-size labels ("S1-8\"") near detection | Selectable text on plan | 0.95 |
| 2 | Schedule NECK SIZE column + tag bubble OCR | Schedule has neck column + readable bubble | 0.85 |
| 3 | OCR a tight crop around detection, look for size pattern | Any raster plan | 0.80 (capped by OCR conf) |
| 4 | CFM-range table lookup | Schedule has CFM range table | 0.55 |
| 5 | Output blank + explicit "needs review" flag | Nothing matched | 0.00 |

**Build order:** Level 1 first (highest value, smallest scope), validate against Busy Bees, then Levels 2-5.

---

## 5. Confidence scoring discipline

The number must mean "predicted accuracy" — not arbitrary internal score.

**Surface to estimator:** 3-tier color system + ONE-word source. Hide raw numbers
unless someone explicitly clicks for detail. Decided 2026-06-04 — simpler UX, faster
adoption, no estimator training burden on numeric scores.

| Tier | Range | Excel rendering | Estimator sees | Estimator action |
|---|---|---|---|---|
| HIGH | ≥ 0.80 | normal text | 🟢 trust | skip |
| MEDIUM | 0.50 – 0.80 | yellow highlight | 🟡 verify | spot-check |
| LOW | 0.15 – 0.50 | red `?` cell | 🔴 unknown | manual fill |
| (dropped) | < 0.15 | not emitted | nothing | n/a |

**Internal:** keep the raw float, the source label (`plan`, `schedule`, `ocr`, `guess`),
and per-level evidence — all for debugging and calibration. Never the estimator's
primary view.

**Calibration:** 50+ ground-truth PDFs confirmed available 2026-06-04. Phase 3 will
fit isotonic regression curve. Measure Brier score; target < 0.20.

**Feedback loop:** every estimator correction updates per-level calibration factors automatically.

---

## 6. Success metrics (for evaluating each cycle)

Only three numbers matter:

1. **Per-tag count accuracy** — for tags we identify, how close to team's count
2. **Coverage** — fraction of team's manual entries auto-populated
3. **False positive rate** — fraction of entries estimator must delete

**Bar to clear:** 80% accuracy / 70% coverage / <15% FP rate.
**Below the bar → not estimator-usable. Don't ship.**

---

## 7. Plan: 6 weeks, 4 phases

### Phase 1 — Define (week 1)

- Day 1-2: write/refine this doc
- Day 3: `/plan-ceo-review` + `/plan-eng-review` + `/plan-devex-review`
- Day 4: `/codex consult` for independent challenge
- Day 5: address feedback, freeze plan

### Phase 2 — Build in slices (weeks 2-4)

**Slice 1 — Neck-size Level 1 (text-layer)** — week 2
**Slice 2 — Neck-size Levels 2-3 (schedule + OCR)** — week 3
**Slice 3 — Neck-size Levels 4-5 + calibration** — week 4

After each slice: decision gate. Do the numbers prove it works? If no, stop.

### Phase 3 — Validate (week 5)

- Run on 20 real PDFs with known team takeoffs
- Measure per-PDF: count accuracy, coverage, FP rate
- `/qa-only` for inspection, `/codex challenge` for adversarial review

### Phase 4 — Ship or iterate (week 6)

- If validation passes the bar: `/setup-deploy`, `/land-and-deploy`, `/canary`
- If not: `/retro`, document gap, restart Phase 2 with focused fix

---

## 8. Team coordination

| When | Skill | Purpose |
|---|---|---|
| Start of workday | `/context-restore` | Pick up where left off |
| End of workday | `/context-save` | Hand-off state |
| Before destructive work | `/careful` / `/guard` | Prevent accidents |
| Debugging | `/investigate` | Root cause, not patches |
| Pre-merge | `/code-review` + `/review` | Two-layer review |
| Weekly | `/retro` | Document what shipped |
| Stuck on decision | `/codex consult` | Second opinion |

---

## 9. What we explicitly stop doing

- **Adding new features** until validation proves current ones work
- **Defending old test results** — quoted Busy Bees numbers were standalone, not from delivered files
- **Fixing my own bugs without measuring impact** — root-cause first, fix once

---

## 10. Open decisions for the team

1. **Threshold-setting policy** — who decides the 0.80 / 0.50 / 0.15 thresholds? Per-estimator override?
2. **Calibration cadence** — monthly? After every N corrections?
3. **Conflict resolution** — when Level 1 (plan label) and Level 2 (schedule) disagree, which wins?
4. **Output format** — match team's exact Excel format or add confidence columns?
5. **Rollout** — internal team first or external pilot?

These need answers before Phase 2 ships.

---

## 11. Honest risks

- **YOLO under-detection** — if it doesn't find symbols, the waterfall has nothing to work with.
  Waterfall output looks confident even when 80% of equipment is silently missing. **Phase 3
  must measure DETECTION coverage separately from waterfall accuracy.**
- **YOLO over-detection (phantoms)** — waterfall will happily extract neck size for non-existent
  equipment. Must compare counts against team takeoffs in Phase 3.
- **Waterfall accuracy ceiling = tagging accuracy** — multiplicative dependency. Waterfall is
  meaningless if tag inference fails on a drawing.
- **YOLO retrain breaks calibration** — calibration curve fit on v10 outputs becomes stale when
  v11 ships. Mitigation: re-calibrate as a required step in any retrain cycle.
- **OCR noise on small labels** — 6-point font barely readable; affects Levels 1, 3.
- **Estimator adoption** — if they don't trust the output, they won't submit corrections, and
  the loop never closes.

---

## 12. References

- Schedule reading: `saas/backend/schedule_ocr.py`, `schedule_parser.py`
- YOLO detection: `takeoff_cli.py`, `models/hvac_yolov8s_v10.pt`
- Tag inference: `tag_inference.py` (Level 1, 2a, 2b, 2b', 3)
- Bubble detector: `tag_matcher.py`, `models/hvac_tag_detector_v1.pt`
- Correction loop: `saas/backend/api/routes.py` POST `/api/jobs/{id}/correction`
- Comparison tool: `saas/backend/compare_excel.py`
