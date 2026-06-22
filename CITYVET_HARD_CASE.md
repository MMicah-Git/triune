# CityVet Verrado — The Hard Case

**File:** `HVAC CityVet Buckeye_Issued for Construction_Rev 1.pdf` (Plans_Specs)

CityVet is the honest boundary of automated takeoff today: a **broken-font CAD
export** whose schedule text is stored as corrupted / outlined glyphs, so it
cannot be read as text at all. It's the case that shows where extraction hits
its limit — and exactly what we're building OCR to solve.

---

## The one-liner

> "CityVet is a broken-font CAD export — the schedule text is stored as
> corrupted/outlined glyphs, so it can't be read as text. It's the hard case
> that shows where automated extraction hits its limit, and it's exactly what
> we're building schedule-OCR to solve."

---

## The evidence

| What the pipeline did | Result |
|---|---|
| Detected schedule tables on the sheets | ✅ **3 tables found** — structure detection works |
| Extracted tags from the text layer | ❌ **0 tags** — font encoding corrupted (known CAD-export problem) |
| Fell back to OCR (reading the rendered pixels) | ⚠️ recovered only fragments (`RE-3`, `RE4`, `RF-2`), not the real items |

**What the takeoff actually needs (from the completed human takeoff):**
`A`, `B`, `C`, `D` (diffusers), `ERV-1`, `MVD`, `ROOF CAP`, `WL-1,2`

**Overlap between OCR result and the real takeoff:** zero. The corruption is too
deep for off-the-shelf OCR on this file, and the fragments it did read came from
an "existing to remain" note, not the new-work schedule.

---

## Why this is a strong slide, not a weak one

- **It's honest.** Showing the boundary builds trust — the tool tells you when it
  can't read something instead of silently producing garbage.
- **It degrades gracefully.** Detect tables → try OCR → flag it. No fabricated
  numbers.
- **It gives a concrete roadmap item.** Schedule-OCR robust enough to read
  corrupted CAD fonts is the next frontier. The pipeline already detects the
  tables and triggers OCR automatically; the remaining work is making that OCR
  read this quality of input (region-targeting / a document-VLM parser).

---

## The point it makes

> "On clean drawings — like the two PNC sets — the tool produces the takeoff in
> ~3 minutes. CityVet defines our next frontier: schedule OCR robust enough to
> read corrupted CAD fonts. The system already detects the tables and triggers
> OCR on its own; the work now is making that OCR read this quality of input."

---

## Suggested 3-file arc

1. **PNC Medical** ✅ — clean drawing → full air-device takeoff in minutes.
2. **Atascocita** ✅ — different project, same result → it generalizes.
3. **CityVet** ⚠️ — the hard case that defines what's next.

Working → repeatable → honest roadmap.

---

## Technical note (for engineers)

The pipeline change that surfaces this gracefully: `takeoff_cli.py` now triggers
the schedule-OCR fallback when the text layer yields **fewer than 5 tags** (a
broken-font export produces a handful of garbage tags, not zero), and only
replaces the text-layer result when OCR recovers strictly more. CityVet exercises
that path — OCR runs, but the source quality defeats it. Fixing CityVet-class
files is the schedule-OCR robustness work (region-targeting on the detected
tables, or a document-VLM parser), tracked as the next step after the v19s
detection retrain.
