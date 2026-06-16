"""
train_v11.py

Fine-tune the v10 model on our Bluebeam-derived dataset.

Run locally as a smoke test (CPU, 2-3 epochs, will take a long time but
verifies the pipeline):
    python train_v11.py --epochs 2 --device cpu --batch 4

Run on Kaggle T4 GPU (in a notebook cell):
    !python train_v11.py --epochs 60 --device 0 --batch 16 --imgsz 1280

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
    'data_yaml': 'yolo_dataset_v11/data.yaml',
    'project_dir': 'runs',                # ultralytics appends 'detect/' itself
    'run_name': 'hvac_v11',
    'epochs': 50,
    'imgsz': 1280,         # full-page training; tile later if needed
    'batch': 8,
    'device': '',          # '' = auto, 'cpu' to force CPU, '0' for GPU
    'patience': 15,        # early stop
    'optimizer': 'AdamW',  # pin optimizer (auto ignores lr0)
    'lr0': 0.0005,         # low LR for fine-tuning from v10
    'mosaic': 0.0,         # disable mosaic on technical drawings
    'mixup': 0.0,
    'copy_paste': 0.0,
    'degrees': 0.0,        # blueprints have a fixed orientation
    'shear': 0.0,
    'perspective': 0.0,
    'fliplr': 0.5,         # horizontal flip is OK
    'flipud': 0.0,         # vertical flip would invert "supply" vs "return" symbols
    'hsv_h': 0.0,
    'hsv_s': 0.0,
    'hsv_v': 0.05,
}


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
        out = Path('models') / 'hvac_yolov8s_v11.pt'
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, out)
        print(f'\nCopied {best} -> {out}')
    else:
        print('\nWARNING: best.pt not found under', args.project_dir)


if __name__ == '__main__':
    main()
