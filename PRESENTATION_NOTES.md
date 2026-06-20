# Titus VP Presentation — Study Guide & Talk Track

Deck: `HVAC_AI_Takeoff_Titus.pptx` (15 slides, speaker notes on each).
Read this end-to-end once; it's everything you need to present confidently and answer questions.

---

## 30-second elevator pitch
"We built an AI that reads HVAC construction drawings and produces a quantified, specced takeoff —
it detects the diffusers, grilles, dampers, and units on the plans, matches each to the equipment
schedule for its specs, and outputs the Bill of Materials in our team's format. It learns from our
estimators' corrections, so it gets better with every project. Its strongest capability is exactly
the air-distribution category Titus makes — and it already extracts neck size, CFM, and model per
device, which is a natural bridge to Titus Selection Navigator."

---

## The key numbers (memorize these)
| Metric | Number | What it means |
|---|---|---|
| Page selection recall | **1.00** | We never drop a real plan page (measured) |
| Detection — object recall | **84%** (held-out) | We find ~84% of the equipment on plans the model never saw |
| Detection — precision | **94%** (held-out) | 94% of what we flag is real (few false positives) |
| Air distribution share | **~84% of all equipment** | The bulk of a takeoff IS Titus's product category |
| Training data mined | **279 projects → ~46,000 tiles** | From our own completed takeoffs |
| Miss-class fix | RTU 6→435, Fire-smoke damper 6→823 | Equipment we were blind to, now learnable |
| Safety bar (the "gate") | **F1 0.889** | A new model must beat this to ship |

> If asked one number: **"84% of the equipment, found at 94% precision, on drawings the model never saw."**

---

## Per-slide talk track (the flow)
1. **Title** — who we are, what it is, why we're here (the Titus partnership angle).
2. **Problem** — manual takeoff is slow/error-prone; hours-to-days per project; mistakes cost bids.
3. **What we built** — AI reads PDF → detect → schedule-match → reconcile → output; learns from corrections. It's a live web app.
4. **Pipeline** — 4 parts, each owns one job + one accuracy number. Only Part 2 is a trained model; rest are reliable rules.
5. **Part 1** — picks the right pages across any firm's style. Recall 1.00; recovered dropped plans; killed phantom pages.
6. **Part 2** — YOLO finds the equipment. 84% object recall / 94% precision. **Key insight: model finds it, schedule specs it.** ← Titus-relevant.
7. **Part 3** — schedule → per-tag specs; tag matching; reconciliation flags misses honestly; product names from the schedule.
8. **Part 4** — Excel/Bluebeam output in team format; correction → retrain → gate → deploy flywheel; never ship a worse model.
9. **Accuracy & trust** — measured on held-out + team takeoffs; the gate; it's an assist, estimator stays in control.
10. **Data advantage** — mined our own takeoffs; compounding proprietary asset; competitors can't replicate the data history.
11. **Status** — what's live now vs the one GPU run away (be honest, be confident).
12. **Why Titus** — our 84% sweet spot is air distribution; we already pull neck/CFM/model; bridge to Selection Navigator.
13. **Future** — close retrain loop → Titus integration → multi-doc → plumbing/electrical → SaaS.
14. **Vision** — drawing in → instant specced manufacturer-ready takeoff.
15. **Discussion** — demo, Selection Navigator integration, data collab, pilot.

---

## Glossary (so you can answer "what is X")
- **Takeoff** — the quantified list of every piece of equipment on the drawings + specs; the input to a bid.
- **YOLO / YOLOv8** — a computer-vision model that finds & boxes objects in an image. Our "eyes" that spot symbols.
- **Detection** — the AI finding equipment symbols on the plan and drawing a box around each.
- **Recall** — of the equipment that's really there, what % did we find. (Miss = low recall.)
- **Precision** — of what we flagged, what % was real. (Phantoms = low precision.)
- **Held-out** — drawings the model was NOT trained on; the honest test of whether it generalizes.
- **The schedule** — the table on the drawings listing each tag's specs (CFM, neck size, model, brand).
- **Tag** — the label next to a symbol (e.g., "S-1", "RTU-3") that ties it to a schedule row.
- **Reconciliation** — comparing what we detected vs what the schedule says should exist; flags gaps.
- **Tile** — we cut big E-size sheets into small 640px squares so the model can see tiny symbols; a "tile" is one such square (a training example).
- **The gate** — the benchmark check that only lets a new model ship if it beats the current one on held-out data.
- **The flywheel** — the self-improving loop: corrections → retrain → gate → deploy → better → repeat.
- **Air distribution / GRD** — Grilles, Registers, Diffusers (+louvers). Titus's core product category; ~84% of a takeoff's count.
- **Selection Navigator** — Titus's product-selection tool; takes specs (neck/CFM) → returns Titus products. The integration target.

---

## Likely VP questions + honest answers
**"How accurate is it really?"**
84% of equipment found at 94% precision on drawings it never trained on. Page selection is 1.00 (we don't
drop plans). It's an assist — it flags what it's unsure of, the estimator verifies. We measure on our own
completed takeoffs, so the numbers are real-world, not lab.

**"Why isn't it 100%?"**
Two reasons, both honest: (1) some equipment looks identical visually — so the schedule, not the model,
supplies the exact type; (2) rare equipment was data-starved — we've now mined hundreds of examples and the
upgrade is one training run away. We deliberately never overstate; the gate blocks any model that isn't truly better.

**"What's the Titus angle / what's in it for us?"**
Our strongest capability is finding and speccing air-distribution products — your core. We already extract
neck size, CFM, and model per device. That's exactly Selection Navigator's input. Drawing → Titus BOM with
selections and pricing means more Titus product specified, faster, more accurately. We'd love to tune the
model to Titus product lines via a data collaboration.

**"How long to [X]?"**
The detection upgrade: a ~30-minute GPU run that's already prepped. A Titus Selection Navigator integration:
a focused project (weeks), gated on API access + product mapping. A pilot: we can scope it this week.

**"Who else does this? / competition?"**
There are takeoff tools, but our edge is (a) a vision+schedule design tuned to HVAC, and (b) a proprietary,
compounding data asset from years of completed takeoffs that we mine automatically. The Titus integration
would be a differentiator no generic tool has.

**"Data privacy / our drawings?"**
Everything runs in our environment; the model learns from our own corrected takeoffs. Any Titus collaboration
would be on agreed terms. (Note: we keep customer drawings private; vision runs locally, not via a public API.)

**"What do you need from us?"**
Ideally: Selection Navigator API/mapping access, and a sample of Titus-spec'd projects to tune to your lines.
Minimum: agree to a pilot and a live demo on a real project.

---

## Honesty guardrails (don't oversell — it builds trust)
- The big detection upgrade is **built and validated but not yet deployed** — it needs one GPU run. Say that plainly.
- The "84%" is **air-device object recall on held-out**; don't quote it as overall per-line-item accuracy.
- The Titus integration is a **proposal/opportunity**, not something already built.
- It's an **estimator assist**, not a replacement — that's a feature, not a weakness.
