"""
train_v11.py

Fine-tune the v10 model on our Bluebeam-derived dataset.

Run locally as a smoke test (CPU, 2-3 epochs, will take a long time but
verifies the pipeline):
    python train_v11.py --epochs 2 --device cpu --batch 4

Run on Kaggle T4 GPU (in a notebook cell):
    !python train_v11.py --epochs 60 --device 0 --batch 16 --imgsz 640

After training, the best weights land at:
    runs/detect/hvac_v11/weights/best.pt
Copy/rename to models/hvac_yolov8s_v11.pt for production.
"""

import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO


DEFAULTS = {
    'base_model': 'models/hvac_yolov8s_v10.pt',
    'data_yaml': 'yolo_dataset_v16/data.yaml',   # v14 base + folded corrections, canonical 33-class
    'project_dir': 'runs',                # ultralytics appends 'detect/' itself
    'run_name': 'hvac_v16',
    'epochs': 50,
    'imgsz': 640,          # MATCH the 640px training tiles + inference tiling
                           # (1280 upsampled tiles 2x and mismatched inference)
    'batch': 8,
    'device': '',          # '' = auto, 'cpu' to force CPU, '0' for GPU
    'patience': 15,        # early stop
    'optimizer': 'AdamW',  # pin optimizer (auto ignores lr0)
    'lr0': 0.0005,         # low LR for fine-tuning from v10
    # --- Augmentation: tuned for out-of-distribution drawing styles ---
    'mosaic': 0.3,         # moderate — context variety without shrinking small symbols too far
    'mixup': 0.0,
    'copy_paste': 0.1,     # densify rare classes (class imbalance: AD-GRD/EXHAUST FAN dominate)
    'scale': 0.5,          # scale jitter — robustness to drawing-scale / line-weight variation
    'translate': 0.1,      # position jitter
    'degrees': 0.0,        # blueprints have a fixed orientation
    'shear': 0.0,
    'perspective': 0.0,
    'fliplr': 0.5,         # horizontal flip is OK
    'flipud': 0.0,         # vertical flip would invert "supply" vs "return" symbols
    'hsv_h': 0.0,
    'hsv_s': 0.0,
    'hsv_v': 0.2,          # brightness variation (scan/render exposure differences)
}


def _make_data_yaml_portable(yaml_path: Path) -> None:
    """Make the dataset portable on any machine (Colab/Kaggle).

    The committed data.yaml stores a machine-absolute `path:`, and the
    train.txt/val.txt image lists store machine-absolute paths too — both break
    when the bundle is unzipped elsewhere. We rewrite `path:` to the yaml's own
    directory, and rewrite each list entry to `{root}/images/{filename}`."""
    root = yaml_path.resolve().parent

    # 1) Rewrite `path:` and collect train/val list-file names.
    lines = yaml_path.read_text(encoding='utf-8').splitlines()
    out, replaced, list_files = [], False, []
    for ln in lines:
        s = ln.strip()
        if s.lower().startswith('path:'):
            out.append(f'path: {root.as_posix()}')
            replaced = True
        else:
            out.append(ln)
            if s.lower().startswith(('train:', 'val:')):
                val = s.split(':', 1)[1].strip()
                if val.endswith('.txt'):
                    list_files.append(val)
    if not replaced:
        out.insert(0, f'path: {root.as_posix()}')
    yaml_path.write_text('\n'.join(out) + '\n', encoding='utf-8')

    # 2) Rewrite each list file's entries to the runtime images dir.
    for lf in list_files:
        lp = root / lf
        if not lp.exists():
            continue
        rewritten = []
        for entry in lp.read_text(encoding='utf-8').splitlines():
            entry = entry.strip()
            if not entry:
                continue
            rewritten.append(f'{root.as_posix()}/images/{Path(entry).name}')
        lp.write_text('\n'.join(rewritten) + '\n', encoding='utf-8')

    print(f'Dataset root (portable): {root.as_posix()}  '
          f'(rewrote {len(list_files)} list file(s))')


def main():
    ap = argparse.ArgumentParser(description='Fine-tune v10 -> v11 on Bluebeam dataset')
    for k, v in DEFAULTS.items():
        ap.add_argument(f'--{k.replace("_", "-")}', type=type(v) if not isinstance(v, bool) else str, default=v)
    ap.add_argument('--no-pretrained', action='store_true',
                    help='Train from scratch (yolov8s.yaml) instead of fine-tuning v10')
    args = ap.parse_args()

    data_yaml = Path(getattr(args, 'data_yaml'))
    if not data_yaml.exists():
        raise SystemExit(f'Missing {data_yaml}. Run prepare_training.py first.')
    _make_data_yaml_portable(data_yaml)

    base = args.base_model if not args.no_pretrained else 'yolov8s.pt'
    print(f'Base model: {base}')
    print(f'Dataset:    {data_yaml}')
    print(f'Epochs:     {args.epochs}')
    print(f'Image size: {args.imgsz}')
    print(f'Batch:      {args.batch}')
    print(f'Device:     {args.device or "auto"}')
    print()

    model = YOLO(base)

    results = model.train(
        data=str(data_yaml),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device or None,
        project=args.project_dir,
        name=args.run_name,
        patience=args.patience,
        optimizer=args.optimizer,
        lr0=args.lr0,
        mosaic=args.mosaic,
        mixup=args.mixup,
        copy_paste=args.copy_paste,
        scale=args.scale,
        translate=args.translate,
        degrees=args.degrees,
        shear=args.shear,
        perspective=args.perspective,
        fliplr=args.fliplr,
        flipud=args.flipud,
        hsv_h=args.hsv_h,
        hsv_s=args.hsv_s,
        hsv_v=args.hsv_v,
        exist_ok=True,
        plots=True,
    )

    # Promote best.pt to models/. Use the trainer's authoritative path rather
    # than reconstructing it — ultralytics nests runs/detect/... in ways that
    # don't match project/name, which silently broke promotion in older runs.
    best = None
    trainer = getattr(model, 'trainer', None)
    if trainer is not None and getattr(trainer, 'best', None) and Path(trainer.best).exists():
        best = Path(trainer.best)
    else:
        # Fallbacks: results.save_dir, then a recursive glob (newest wins).
        save_dir = getattr(results, 'save_dir', None)
        if save_dir and (Path(save_dir) / 'weights' / 'best.pt').exists():
            best = Path(save_dir) / 'weights' / 'best.pt'
        else:
            candidates = sorted(
                Path(args.project_dir).rglob('best.pt'),
                key=lambda p: p.stat().st_mtime,
            )
            if candidates:
                best = candidates[-1]

    if best and best.exists():
        # Name the promoted weights after the run (hvac_v15 -> hvac_yolov8s_v15.pt)
        ver = args.run_name[len('hvac_'):] if args.run_name.startswith('hvac_') else args.run_name
        out = Path('models') / f'hvac_yolov8s_{ver}.pt'
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, out)
        print(f'\nCopied {best} -> {out}')
    else:
        print('\nWARNING: best.pt not found under', args.project_dir)


if __name__ == '__main__':
    main()
