"""Zip the v14 dataset + v10 base model + train script into a Colab bundle."""
import zipfile, os, glob

os.makedirs('colab_bundle', exist_ok=True)
zpath = 'colab_bundle/hvac_v14_colab.zip'
z = zipfile.ZipFile(zpath, 'w', zipfile.ZIP_STORED)
n = 0
for f in glob.glob('yolo_dataset_v14/**/*', recursive=True):
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
print(f'zipped {n} dataset files + v10 model + train_v11.py')
print('bundle:', zpath, f'{os.path.getsize(zpath)/1048576:.0f} MB')
