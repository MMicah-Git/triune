# Scope — Callout / Neck-Size Reader (per-instance sizes on the plan)

**Goal:** produce takeoff rows split by neck/duct size like the team's completed
takeoff — e.g. `S1 → 6 @ 6" + 4 @ 8"`, `MVD → 10 @ 6" + 13 @ 8" + …` — instead of
one coarse row per tag. The neck/duct sizes are **text callouts next to each
symbol on the drawing**, not in the schedule, so they must be read off the plan.

**Target (PNC, real completed takeoff):**
```
S1   6 @ 6"   (24×24)   SUPPLY DIFFUSER
S1   4 @ 8"   (24×24)   SUPPLY DIFFUSER
R1   6 @ 8"  / 3 @ 10"  RETURN DIFFUSER
MVD  10 @ 6" / 13 @ 8" / 4 @ 10" / 4 @ 12×6 / 3 @ 14×6
```

---

## What ALREADY exists (don't rebuild)

`saas/backend/neck_size_waterfall.py` — a **5-level cascade** that returns
`(neck_size, confidence, source, evidence)` per detection:

| Level | Method | Status (per code) |
|---|---|---|
| 1 | Text-layer tag-size label near the box (`S1-8"`, `R1-10X8`) | implemented |
| 2 | Schedule NECK column + tag-bubble OCR | functions present |
| 3 | OCR a crop around the detection, find a size token | functions present |
| 4 | CFM-range table lookup | functions present |
| 5 | Explicit "unknown" | implemented |

Plus `neck_size_waterfall_runner.py` orchestrates it over a job and **mutates each
detection** with `neck_size` / `confidence_tier` / `source`. It's written as
STANDALONE and **not yet wired into the live pipeline.**

So the remaining work is **validate → integrate → group the output → measure**,
not build the reader from zero.

---

## The build (4 slices)

### Slice 1 — Validate the cascade on real ground truth
- Run `neck_size_waterfall_runner` over **PNC Medical** and **Atascocita** (we have
  their completed takeoffs with true neck sizes).
- Score per-detection neck size vs the completed takeoff: % correct, % unknown,
  % wrong, by level. Find which levels actually fire and which are weak.
- Output: a `neck_accuracy.csv` and a go/no-go on each level.
- *Why first:* tells us if the existing cascade is usable or needs real work
  before we integrate anything.

### Slice 2 — Wire it into the pipeline
- Call `extract_neck_size_for_detection()` from `post_takeoff.py` after detections +
  variables are loaded (the docstring's intended hook).
- Each detection gains `neck_size`, `duct_size`, `confidence_tier`, `source`.
- Persist into `*_detections.json` so the Excel + Bluebeam stages can read them.
- *Risk control:* behind a flag; default off until Slice 1 says it's accurate.

### Slice 3 — Group the output by (tag, neck, duct)
- `takeoff_cli.write_excel`: emit one row per **(tag, neck_size, duct_size)** group
  with its own QTY, instead of one row per tag. This is the change that produces
  the `S1 6@6" + 4@8"` breakdown.
- Bluebeam hover already shows `neck=`/`duct=` (just added) — it will now carry the
  per-instance value instead of the schedule value.
- Low confidence sizes render as `?"` / flagged so the estimator verifies, never
  silently wrong.

### Slice 4 — Confidence + correction loop
- Tier each size HIGH/MED/LOW (formulas already in the cascade).
- LOW/unknown rows are flagged for the estimator. Their corrections feed back as
  training/label data (ties into the existing review loop).

---

## Validation plan (the bar)
Score against the completed takeoffs we already have:
- **Per-(tag,size) row recall**: do we produce the right size buckets?
- **Neck-size accuracy on detected items**: of items we sized, what % match?
- **Bar to ship:** neck size correct on ≥80% of HIGH-confidence items, and never
  emit a confident-but-wrong size (precision over recall — abstain to "verify").
- Regression: PNC + Atascocita + one clean prior set.

---

## Dependencies / ordering
1. **Detection recall first** (v19s retrain). Neck sizes only help on items we
   detect — splitting `S1` into 6+4 requires finding all ~10 S1s. Sizing 3 of 10
   is still only 3. So this reader is **most valuable after the detection retrain**,
   though Slices 1–2 can proceed in parallel on whatever is detected today.
2. EasyOCR (already a dependency) for Levels 2/3.

## Risks
- **Callout association ambiguity:** a size label can sit between two symbols.
  Mitigate with the proximity window (already in code) + nearest-symbol assignment.
- **OCR on small callouts** (the same limit that hurts tag bubbles) — Levels 1/2
  (text-layer) are more reliable than Level 3 (OCR); expect Level 3 to be the weak one.
- **Duct size vs neck size confusion** (S4 has both) — keep them in separate fields.

## Effort (rough)
- Slice 1 (validate): ~0.5–1 day.
- Slice 2 (integrate): ~1 day.
- Slice 3 (group output): ~0.5–1 day.
- Slice 4 (confidence/loop): ~1 day.
Total ≈ **3–4 focused days**, gated by Slice 1's accuracy finding. Not an
overnight change; this is the feature that closes the gap to the team's detail.

---

## One-line summary
The reader mostly **exists** (5-level cascade + runner); the build is to **measure
it against the completed takeoffs, wire it into post_takeoff, group the Excel by
tag+size, and gate on accuracy** — best done right after the v19s detection retrain
so there are enough detected instances to size.
