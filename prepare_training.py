"""
prepare_training.py

Take the dataset produced by bluebeam_to_yolo.py and emit the file lists
+ data.yaml needed for an `ultralytics yolo train` run.

Per-project train/val split — never put pages from the same project in
both sets, to keep validation honest.

Usage:
    python prepare_training.py --dataset yolo_dataset_v11 \\
        --val-projects "music-academy-of-the-west-new-music-education-center,citadel-irvine"
"""

import argparse
import json
import random
from pathlib import Path
from collections import defaultdict, Counter


def project_from_stem(stem: str) -> str:
    """Image stem is '<project>__pNNN'. Strip the page suffix."""
    return stem.rsplit('__p', 1)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', default='yolo_dataset_v11')
    ap.add_argument('--val-projects', default='',
                    help='Comma-separated list of project slugs to use for val. '
                         'If empty, --val-frac is used.')
    ap.add_argument('--val-frac', type=float, default=0.2,
                    help='Fraction of projects to use for val if --val-projects empty')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()

    root = Path(args.dataset).resolve()
    images_dir = root / 'images'
    labels_dir = root / 'labels'
    classes_path = root / 'classes.txt'

    if not classes_path.exists():
        raise SystemExit(f'classes.txt not found at {classes_path}')

    classes = [
        ln.strip() for ln in classes_path.read_text(encoding='utf-8').splitlines()
        if ln.strip()
    ]

    # Group images by project
    by_project = defaultdict(list)
    for img in sorted(images_dir.glob('*.png')):
        proj = project_from_stem(img.stem)
        by_project[proj].append(img)

    projects = sorted(by_project)
    print(f'Found {len(projects)} projects with {sum(len(v) for v in by_project.values())} images')

    # Pick val projects
    if args.val_projects:
        val_projects = [p.strip() for p in args.val_projects.split(',') if p.strip()]
        missing = [p for p in val_projects if p not in by_project]
        if missing:
            raise SystemExit(f'Unknown val projects: {missing}\nAvailable: {projects}')
    else:
        rng = random.Random(args.seed)
        n_val = max(1, round(len(projects) * args.val_frac))
        val_projects = rng.sample(projects, n_val)

    val_set = set(val_projects)
    train_projects = [p for p in projects if p not in val_set]

    print()
    print(f'Train projects ({len(train_projects)}):')
    for p in train_projects:
        print(f'  {p}  ({len(by_project[p])} images)')
    print(f'Val projects ({len(val_projects)}):')
    for p in val_projects:
        print(f'  {p}  ({len(by_project[p])} images)')

    # Sanity: every image must have a non-empty label file
    train_imgs, val_imgs = [], []
    train_box_count = val_box_count = 0
    class_counts_split = {'train': Counter(), 'val': Counter()}
    skipped = 0
    for proj in train_projects + val_projects:
        target_list = train_imgs if proj in train_projects else val_imgs
        target_split = 'train' if proj in train_projects else 'val'
        for img in by_project[proj]:
            lbl = labels_dir / (img.stem + '.txt')
            if not lbl.exists() or lbl.stat().st_size == 0:
                skipped += 1
                continue
            target_list.append(img)
            for line in lbl.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line:
                    continue
                cid = int(line.split()[0])
                if proj in train_projects:
                    train_box_count += 1
                else:
                    val_box_count += 1
                if 0 <= cid < len(classes):
                    class_counts_split[target_split][classes[cid]] += 1

    if skipped:
        print(f'Skipped {skipped} images with missing/empty labels')

    print()
    print(f'Train: {len(train_imgs)} images, {train_box_count} boxes')
    print(f'Val:   {len(val_imgs)} images, {val_box_count} boxes')

    # Write file lists (absolute paths, forward slashes for ultralytics)
    train_list = root / 'train.txt'
    val_list = root / 'val.txt'
    train_list.write_text(
        '\n'.join(str(p).replace('\\', '/') for p in train_imgs) + '\n',
        encoding='utf-8',
    )
    val_list.write_text(
        '\n'.join(str(p).replace('\\', '/') for p in val_imgs) + '\n',
        encoding='utf-8',
    )

    # Write data.yaml
    data_yaml = root / 'data.yaml'
    yaml_lines = [
        f'path: {str(root).replace(chr(92), "/")}',
        f'train: train.txt',
        f'val: val.txt',
        '',
        'names:',
    ]
    for i, cls in enumerate(classes):
        # Quote class names since some contain quotes/special chars
        yaml_lines.append(f'  {i}: "{cls}"')
    data_yaml.write_text('\n'.join(yaml_lines) + '\n', encoding='utf-8')

    # Summary card
    print()
    print('=== Class distribution per split ===')
    print(f'{"class":35s} {"train":>7s} {"val":>5s}')
    for cls in classes:
        t = class_counts_split['train'][cls]
        v = class_counts_split['val'][cls]
        warn = '  <- no val samples' if t > 0 and v == 0 else ''
        print(f'{cls:35s} {t:7d} {v:5d}{warn}')

    # Write a split summary json for reproducibility
    summary = {
        'train_projects': train_projects,
        'val_projects': val_projects,
        'train_images': len(train_imgs),
        'val_images': len(val_imgs),
        'train_boxes': train_box_count,
        'val_boxes': val_box_count,
        'classes': classes,
    }
    (root / 'split.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')

    print()
    print(f'Wrote: {train_list.name}, {val_list.name}, {data_yaml.name}, split.json')
    print(f'Use with:  yolo train data={data_yaml} model=...  ')


if __name__ == '__main__':
    main()
