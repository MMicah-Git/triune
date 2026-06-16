"""
package_for_kaggle.py

Bundle the YOLO dataset + training scripts + v10 weights into a single
portable zip ready to upload as a Kaggle dataset.

Rewrites data.yaml / train.txt / val.txt to use paths relative to the
zip root so the bundle works anywhere it's unzipped.

Usage:
    python package_for_kaggle.py

Outputs:
    kaggle_bundle/hvac_v11_training.zip
    kaggle_bundle/notebook_snippet.py     (paste into Kaggle notebook)
"""

import argparse
import shutil
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
# DATASET is set from --dataset in main(); default keeps old behavior.
DATASET = REPO_ROOT / 'yolo_dataset_v11'
MODEL = REPO_ROOT / 'models' / 'hvac_yolov8s_v10.pt'
BUNDLE_DIR = REPO_ROOT / 'kaggle_bundle'
STAGING = BUNDLE_DIR / 'stage'
ZIP_PATH = BUNDLE_DIR / 'hvac_v11_training.zip'

# Files to bundle outside the dataset
EXTRA_FILES = [
    'train_v11.py',
    'class_normalization.py',
    'bluebeam_to_yolo.py',
    'prepare_training.py',
    'verify_dataset.py',
]


def clean_dir(p: Path):
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True, exist_ok=True)


def stage_dataset():
    """Copy dataset into staging with relative paths."""
    src_imgs = DATASET / 'images'
    src_lbls = DATASET / 'labels'
    dst_root = STAGING / DATASET.name
    (dst_root / 'images').mkdir(parents=True, exist_ok=True)
    (dst_root / 'labels').mkdir(parents=True, exist_ok=True)

    img_names = []
    for img in sorted(src_imgs.glob('*.png')):
        shutil.copy2(img, dst_root / 'images' / img.name)
        img_names.append(img.name)
        lbl = src_lbls / (img.stem + '.txt')
        if lbl.exists():
            shutil.copy2(lbl, dst_root / 'labels' / lbl.name)

    # Copy classes.txt + split.json verbatim
    for sidecar in ('classes.txt', 'split.json'):
        s = DATASET / sidecar
        if s.exists():
            shutil.copy2(s, dst_root / sidecar)

    # Rewrite train.txt / val.txt with relative paths
    for split_name in ('train', 'val'):
        src_list = DATASET / f'{split_name}.txt'
        if not src_list.exists():
            continue
        entries = [
            line.strip() for line in src_list.read_text(encoding='utf-8').splitlines()
            if line.strip()
        ]
        rel_entries = [f'images/{Path(p).name}' for p in entries]
        (dst_root / f'{split_name}.txt').write_text(
            '\n'.join(rel_entries) + '\n', encoding='utf-8'
        )

    # Rewrite data.yaml to use relative path
    classes = (DATASET / 'classes.txt').read_text(encoding='utf-8').strip().splitlines()
    yaml_lines = [
        '# Path is relative to this yaml; train.txt / val.txt are relative to path.',
        'path: .',
        'train: train.txt',
        'val: val.txt',
        '',
        'names:',
    ]
    for i, cls in enumerate(c.strip() for c in classes if c.strip()):
        yaml_lines.append(f'  {i}: "{cls}"')
    (dst_root / 'data.yaml').write_text('\n'.join(yaml_lines) + '\n', encoding='utf-8')


def stage_model_and_scripts():
    (STAGING / 'models').mkdir(parents=True, exist_ok=True)
    if MODEL.exists():
        shutil.copy2(MODEL, STAGING / 'models' / MODEL.name)
    else:
        print(f'WARNING: {MODEL} missing; the Kaggle run needs you to upload v10 separately.')

    for fname in EXTRA_FILES:
        s = REPO_ROOT / fname
        if s.exists():
            shutil.copy2(s, STAGING / fname)
        else:
            print(f'WARNING: {fname} missing in repo, skipping.')


def write_zip():
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, 'w', zipfile.ZIP_STORED) as zf:
        for p in STAGING.rglob('*'):
            if p.is_file():
                zf.write(p, p.relative_to(STAGING))


NOTEBOOK_SNIPPET_TEMPLATE = '''\
# === HVAC v11 training on Kaggle T4 ===
# 1. Add this notebook's dataset:  {kaggle_name}  (created from {zip_name})
#    The bundle unpacks to /kaggle/input/{kaggle_name}/
# 2. Enable GPU:  Settings -> Accelerator -> GPU T4 x1
# 3. Run the cells below.

import os, shutil, subprocess, sys
from pathlib import Path

SRC = Path('/kaggle/input/{kaggle_name}')
WORK = Path('/kaggle/working')

# Copy scripts + dataset + model into /kaggle/working (writable)
for p in ('train_v11.py', 'class_normalization.py'):
    shutil.copy2(SRC / p, WORK / p)
shutil.copytree(SRC / '{dataset_dir}', WORK / '{dataset_dir}', dirs_exist_ok=True)
shutil.copytree(SRC / 'models', WORK / 'models', dirs_exist_ok=True)

# Install ultralytics if not already in the Kaggle image
subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'ultralytics'], check=True)

os.chdir(WORK)
subprocess.run([
    sys.executable, '-u', 'train_v11.py',
    '--data-yaml', '{dataset_dir}/data.yaml',
    '--epochs', '60',
    '--batch', '16',
    '--imgsz', '1280',
    '--device', '0',
    '--optimizer', 'AdamW',
    '--lr0', '0.0005',
], check=True)

# Copy weights out for download
final = WORK / 'models' / 'hvac_yolov8s_v11.pt'
print('Final model:', final, 'exists:', final.exists())
'''


def main():
    global DATASET
    ap = argparse.ArgumentParser(description='Bundle a YOLO dataset for Kaggle training')
    ap.add_argument('--dataset', default='yolo_dataset_v11',
                    help='Dataset dir name under repo root (e.g. yolo_dataset_v13)')
    ap.add_argument('--kaggle-name', default='hvac-v11-training',
                    help='Name to give the Kaggle dataset (used in the notebook snippet)')
    args = ap.parse_args()

    DATASET = REPO_ROOT / args.dataset
    if not DATASET.exists():
        raise SystemExit(f'Dataset missing: {DATASET}. Run bluebeam_to_yolo.py first.')

    clean_dir(BUNDLE_DIR)
    clean_dir(STAGING)

    print(f'Staging dataset ({DATASET.name})...')
    stage_dataset()
    print('Staging model + scripts...')
    stage_model_and_scripts()

    print('Writing zip...')
    write_zip()

    snippet_path = BUNDLE_DIR / 'notebook_snippet.py'
    snippet_path.write_text(
        NOTEBOOK_SNIPPET_TEMPLATE.format(
            kaggle_name=args.kaggle_name,
            zip_name=ZIP_PATH.name,
            dataset_dir=DATASET.name,
        ),
        encoding='utf-8',
    )

    # Cleanup staging
    shutil.rmtree(STAGING)

    size_mb = ZIP_PATH.stat().st_size / 1024 / 1024
    print()
    print(f'Bundle written: {ZIP_PATH} ({size_mb:.1f} MB)')
    print(f'Notebook snippet: {snippet_path}')
    print()
    print('Next steps:')
    print('  1. https://www.kaggle.com/datasets -> "+ New Dataset"')
    print(f'     Upload {ZIP_PATH.name}, name it "{args.kaggle_name}"')
    print('  2. https://www.kaggle.com/code -> "+ New Notebook"')
    print(f'     Sidebar -> Input -> Add Data -> your {args.kaggle_name} dataset')
    print('     Settings -> Accelerator -> GPU T4 x1')
    print('  3. Paste contents of notebook_snippet.py into a cell, Run All')
    print('  4. When training finishes (~45-90 min), download:')
    print('     /kaggle/working/models/hvac_yolov8s_v11.pt')


if __name__ == '__main__':
    main()
