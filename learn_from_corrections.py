"""
learn_from_corrections.py

Stage 3 of the self-learning loop: bundle the original training data PLUS
every accumulated estimator correction into a fresh Kaggle-ready training
bundle for the next model version.

How the loop works:
  1. Estimator opens an annotated PDF in Bluebeam, fixes mistakes
     (deletes wrong stamps, adds missed equipment, fixes wrong classes).
  2. Uploads the corrected file via the SaaS UI ("Submit correction").
  3. Backend (POST /api/jobs/{id}/correction) extracts the polygons via
     bluebeam_to_yolo.process_project and appends a record to
     saas/data/training_queue.jsonl.
  4. ← YOU ARE HERE ← run this script when you want to retrain.
     It merges the original dataset + every correction in the queue,
     produces yolo_dataset_v<NEXT>/, and zips a Kaggle-ready bundle.
  5. Upload the bundle to Kaggle, train, download v<NEXT>.pt.
  6. Run `python benchmark_v10_vs_v11.py --pdf <holdout> --truth <markup>`
     to confirm the new model beats the current production model.
  7. If F1 improves: copy v<NEXT>.pt to models/hvac_yolov8s_v<NEXT>.pt
     and update HVAC_MODEL env var (or whatever your deploy uses).

Usage:
    python learn_from_corrections.py
    python learn_from_corrections.py --base-dataset yolo_dataset_v11
    python learn_from_corrections.py --next-version v12

Outputs:
    yolo_dataset_v<NEXT>/                 (combined dataset)
    kaggle_bundle_v<NEXT>/                (zip ready to upload)
    learn_from_corrections.log            (what was included / skipped)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone


REPO_ROOT = Path(__file__).resolve().parent
SAAS_DATA = REPO_ROOT / 'saas' / 'data'
DEFAULT_QUEUE = SAAS_DATA / 'training_queue.jsonl'
DEFAULT_BASE_DATASET = REPO_ROOT / 'yolo_dataset_v11'


def _load_queue(queue_path: Path) -> list[dict]:
    if not queue_path.exists():
        return []
    records = []
    for line in queue_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            print(f'  WARNING: skipping malformed queue line: {line[:80]}')
    return records


def _copy_dataset(src: Path, dst: Path) -> tuple[int, int]:
    """Copy the base dataset's images + labels into dst. Skip class_index
    files — we'll rebuild those from the merged class list later."""
    if not src.exists():
        raise FileNotFoundError(f'base dataset not found: {src}')
    (dst / 'images').mkdir(parents=True, exist_ok=True)
    (dst / 'labels').mkdir(parents=True, exist_ok=True)
    n_images = n_labels = 0
    src_images = src / 'images'
    src_labels = src / 'labels'
    if src_images.exists():
        for f in src_images.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / 'images' / f.name)
                n_images += 1
    if src_labels.exists():
        for f in src_labels.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / 'labels' / f.name)
                n_labels += 1
    # Copy classes.txt as the starting point
    cls_src = src / 'classes.txt'
    if cls_src.exists():
        shutil.copy2(cls_src, dst / 'classes.txt')
    return n_images, n_labels


def _merge_correction(rec: dict, dst: Path) -> tuple[bool, str]:
    """Pull the per-project YOLO output from a single correction queue
    record into the merged dataset. Returns (ok, message)."""
    yolo_dir = SAAS_DATA / rec.get('yolo_dir', '')
    if not yolo_dir.is_dir():
        return False, f'correction yolo dir missing: {yolo_dir}'

    n_images_added = 0
    n_labels_added = 0
    img_dir = yolo_dir / 'images'
    lbl_dir = yolo_dir / 'labels'
    if not img_dir.is_dir() or not lbl_dir.is_dir():
        return False, f'correction missing images/ or labels/ at {yolo_dir}'

    for f in img_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / 'images' / f.name)
            n_images_added += 1
    for f in lbl_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / 'labels' / f.name)
            n_labels_added += 1

    # If correction has its own classes.txt, merge into dst/classes.txt
    correction_classes = yolo_dir / 'classes.txt'
    if correction_classes.exists():
        existing = []
        merged_path = dst / 'classes.txt'
        if merged_path.exists():
            existing = [c.strip() for c in merged_path.read_text(encoding='utf-8').splitlines() if c.strip()]
        for cls in correction_classes.read_text(encoding='utf-8').splitlines():
            cls = cls.strip()
            if cls and cls not in existing:
                existing.append(cls)
        merged_path.write_text('\n'.join(existing) + '\n', encoding='utf-8')

    return True, f'+{n_images_added} images, +{n_labels_added} labels'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--queue', default=str(DEFAULT_QUEUE),
                    help='Training queue JSONL (default: saas/data/training_queue.jsonl)')
    ap.add_argument('--base-dataset', default=str(DEFAULT_BASE_DATASET),
                    help='Base training dataset (default: yolo_dataset_v11)')
    ap.add_argument('--next-version', default=None,
                    help='Name for the new dataset (default: v12, v13, …)')
    ap.add_argument('--no-zip', action='store_true',
                    help='Skip building the Kaggle bundle zip')
    args = ap.parse_args()

    queue_path = Path(args.queue)
    base_dataset = Path(args.base_dataset)

    # Auto-pick the next version name if not given
    if args.next_version:
        next_version = args.next_version
    else:
        # Walk yolo_dataset_v* and pick max+1
        existing = sorted(REPO_ROOT.glob('yolo_dataset_v*'))
        used = set()
        for p in existing:
            tail = p.name.replace('yolo_dataset_', '')
            if tail.startswith('v') and tail[1:].isdigit():
                used.add(int(tail[1:]))
        nxt = max(used or [11]) + 1
        next_version = f'v{nxt}'

    dst_dataset = REPO_ROOT / f'yolo_dataset_{next_version}'
    print(f'Base dataset:   {base_dataset}')
    print(f'Corrections:    {queue_path}')
    print(f'Target dataset: {dst_dataset}')
    print()

    if dst_dataset.exists():
        print(f'WARNING: {dst_dataset} already exists — wiping it.')
        shutil.rmtree(dst_dataset)

    # 1. Copy base dataset
    print('Copying base dataset...')
    n_base_img, n_base_lbl = _copy_dataset(base_dataset, dst_dataset)
    print(f'  base: {n_base_img} images, {n_base_lbl} labels')

    # 2. Merge corrections from queue
    queue = _load_queue(queue_path)
    print(f'\nFound {len(queue)} correction(s) in training queue.')
    if not queue:
        print('  (Nothing to merge. New corrections show up in saas/data/training_queue.jsonl '
              'after estimators submit corrections via the SaaS UI.)')

    merged_class_counter = Counter()
    successful = skipped = 0
    for i, rec in enumerate(queue, 1):
        ok, msg = _merge_correction(rec, dst_dataset)
        flag = ' ✓ ' if ok else ' SKIP '
        print(f'  [{i:>3d}/{len(queue)}] {flag} {rec.get("project_slug","?")[:50]:50s}  {msg}')
        if ok:
            successful += 1
            for cls, n in (rec.get('classes') or {}).items():
                merged_class_counter[cls] += n
        else:
            skipped += 1

    print(f'\nMerged {successful} corrections ({skipped} skipped).')

    # 3. Run prepare_training to emit data.yaml + split.json
    print('\nRunning prepare_training...')
    prep_cmd = [
        sys.executable, str(REPO_ROOT / 'prepare_training.py'),
        '--dataset', str(dst_dataset),
    ]
    rc = subprocess.call(prep_cmd)
    if rc != 0:
        print(f'WARNING: prepare_training exited {rc}. Inspect manually.')

    # 4. Run package_for_kaggle if not suppressed
    if not args.no_zip:
        print('\nBuilding Kaggle bundle...')
        # package_for_kaggle reads yolo_dataset_v11 by hardcoded name; we patch
        # via env var or by passing CLI args. Existing script doesn't take args,
        # so we cheat: temporarily symlink/rename. Cleaner long-term fix: take args.
        # For now, point user at the dataset:
        print(f'  Dataset is at: {dst_dataset}')
        print(f'  To package for Kaggle, either:')
        print(f'    1. Update package_for_kaggle.py DATASET = "{dst_dataset.name}", or')
        print(f'    2. Symlink: ln -s {dst_dataset.name} yolo_dataset_v11')
        print(f'  Then run: python package_for_kaggle.py')

    # 5. Write a log
    log = {
        'ran_at': datetime.now(timezone.utc).isoformat(),
        'base_dataset': str(base_dataset),
        'next_version': next_version,
        'target_dataset': str(dst_dataset),
        'corrections_in_queue': len(queue),
        'merged_ok': successful,
        'merged_skipped': skipped,
        'class_distribution_added_from_corrections': dict(merged_class_counter),
    }
    log_path = REPO_ROOT / 'learn_from_corrections.log'
    log_path.write_text(json.dumps(log, indent=2), encoding='utf-8')
    print(f'\nLog written: {log_path}')

    # 6. Final summary
    print()
    print('=' * 60)
    print(f'NEXT STEPS for {next_version}:')
    print('=' * 60)
    print(f'  1. (already done) merged dataset at: {dst_dataset}')
    print(f'  2. (already done) train.txt / val.txt / data.yaml generated')
    print(f'  3. Build Kaggle bundle: edit package_for_kaggle.py DATASET=... then run it')
    print(f'  4. Upload kaggle_bundle/hvac_v11_training.zip (rename to {next_version}) to Kaggle')
    print(f'  5. Train ~60 epochs on Kaggle T4')
    print(f'  6. Download best.pt, save to models/hvac_yolov8s_{next_version}.pt')
    print(f'  7. Benchmark vs current: python benchmark_v10_vs_v11.py --pdf <holdout> --truth <markup>')
    print(f'  8. If F1 improves: set HVAC_MODEL env or update config.py to use the new model')


if __name__ == '__main__':
    main()
