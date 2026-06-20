# HVAC AI Takeoff Tool — Executive Summary

**Triune Solutions** · Prepared for Titus · June 2026

---

## The opportunity

HVAC estimators build takeoffs by hand — counting every diffuser, grille, register, damper, fan, and
unit across dozens of large-format drawing sheets, looking each tag up in the equipment schedule for its
specs (CFM, neck size, model, brand), and recording it. A single project takes **hours to days**, and
missed or mis-specced items directly cost bids and margin.

We built an AI tool that does this automatically — and its strongest capability is exactly the
**air-distribution category Titus manufactures.**

## What we built

An AI system that reads a drawing-set PDF and produces a **quantified, specced takeoff**:

> **Ingest** the drawings → **Detect** equipment with computer vision → **Match** each item to its tag
> and the schedule → **Reconcile** counts and flag discrepancies → **Output** an Excel takeoff +
> Bluebeam-ready PDF — in the estimating team's exact format. It **learns from estimator corrections.**

It runs today as a live web application the estimating team uses.

## Results (measured on real projects)

- **Page selection: 100% recall** — it never drops a real plan sheet.
- **Detection: 84% recall at 94% precision** on held-out drawings the model never trained on.
- **~84% of all equipment on a takeoff is air distribution** — Titus's core product line.
- **Specs from the schedule:** neck size, CFM, model, and brand captured per device.
- **A proprietary data advantage:** we mined **279 of Triune's completed takeoffs → ~46,000 labeled
  training examples.** The model learns from real Triune work — a compounding asset competitors can't copy.
- **A safety-gated, self-improving loop:** new models are auto-tested on held-out drawings and only
  deployed if they measurably beat the current one. *(In practice it has already rejected a candidate
  that wasn't an improvement — protecting accuracy.)*

## Why this matters to Titus

- The tool's sweet spot **is** air distribution — grilles, registers, diffusers, louvers.
- It already extracts the exact inputs **Titus Selection Navigator** needs: **neck size and CFM.**
- A natural integration: **drawing in → Titus product BOM out**, with selections and pricing.
- Faster, more accurate takeoffs mean **more Titus product correctly specified and ordered.**

## Status today

- **Live and in use** on the production model — accurate page selection, detection, schedule-driven
  specs, fewer false positives, and honest flagging of anything it's unsure of.
- A **detection upgrade is built and validated**, one short GPU training run from deployment.
- The architecture is modular and measured, so it improves predictably.

## The roadmap

1. **Close the detection upgrade** → higher recall across all equipment classes.
2. **Titus Selection Navigator integration** → automatic Titus BOM, selections, and pricing from a drawing.
3. **Multi-document understanding** → spec books, addenda, Division 23.
4. **Expand the engine** to plumbing and electrical takeoffs.
5. **SaaS productization** for HVAC contractors and suppliers.

## Proposed next steps

- A **live demo** on a real, Titus-heavy project.
- A scoped **Selection Navigator integration** (product mapping + pricing).
- A **data collaboration** to tune the model to Titus product lines.
- Define a **pilot**: scope, success metrics, timeline.

---

*Internal/partner summary — Triune Solutions. Figures are measured on held-out drawings and the team's
own completed takeoffs.*
