"""Zip the latest yolo_dataset_v* + v10 base model + train script into a Colab bundle.

Picks the highest-numbered yolo_dataset_v<N> by default (so it follows
learn_from_corrections output), or pass a dataset name as argv[1].
"""
import zipfile, os, glob, re, sys

if len(sys.argv) > 1:
    dataset = sys.argv[1].rstrip('/').replace('\\', '/').split('/')[-1]
else:
    cands = []
    for d in glob.glob('yolo_dataset_v*'):
        m = re.match(r'yolo_dataset_v(\d+)$', os.path.basename(d))
        if m and os.path.isdir(d):
            cands.append((int(m.group(1)), os.path.basename(d)))
    if not cands:
        raise SystemExit('No yolo_dataset_v<N> found.')
    dataset = max(cands)[1]

os.makedirs('colab_bundle', exist_ok=True)
zpath = f'colab_bundle/hvac_{dataset.replace("yolo_dataset_", "")}_colab.zip'
z = zipfile.ZipFile(zpath, 'w', zipfile.ZIP_STORED)
n = 0
for f in glob.glob(f'{dataset}/**/*', recursive=True):
    if not os.path.isfile(f):
        continue
    norm = f.replace(os.sep, '/')
    if '/annotations/' in norm or f.endswith('.cache'):
        continue
    z.write(f)
    n += 1
z.write('models/hvac_yolov8s_v10.pt')
z.write('train_v11.py')
z.close()
print(f'dataset: {dataset}')
print(f'zipped {n} dataset files + v10 model + train_v11.py')
print('bundle:', zpath, f'{os.path.getsize(zpath)/1048576:.0f} MB')
