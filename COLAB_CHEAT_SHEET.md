# Colab cheat sheet — train v19 (no Drive, no phone)

**Two files you need (in this repo folder):**
- Notebook: `colab_train_v19.ipynb`
- Bundle:   `colab_bundle\hvac_v19s_tiled_colab.zip`  (939 MB)

Sign in to Google as **care@triunesolutions.com** in your browser first.

---

## Click-by-click

1. Go to **colab.research.google.com**
2. **File → Upload notebook** → pick `colab_train_v19.ipynb`
3. **Runtime → Change runtime type → Hardware accelerator = GPU (T4) → Save**
4. **Runtime → Run all**
5. **Cell 1** prints `GPU: Tesla T4` → good. (If it says `NONE`, redo step 3, then Run all again.)
6. **Cell 3** shows a **"Choose Files"** button → click it → pick
   `hvac_v19s_tiled_colab.zip` → wait for the upload bar to finish (a few minutes; **keep the tab open**).
7. **Cell 4** trains (~25–40 min). You'll see per-epoch lines like `Epoch 12/60 …`. Leave it running.
8. **Cell 5** auto-downloads **`hvac_yolov8s_v19.pt`** to your computer (check your Downloads folder).

---

## Bring it home (then you're done)

9. Move that file into the repo as:
   `…\hvac-takeoff-tool-master\models\hvac_yolov8s_v19.pt`
10. That's it — a watcher auto-runs the gate and reports whether it ships. (Nothing else to type.)

---

## If something hiccups
- **Cell 1 says NONE** → GPU not on → Runtime → Change runtime type → GPU → Run all.
- **Upload stalls/fails at cell 3** → your connection choked on 939 MB. Tell me — I'll give you a `wget` one-liner from a share link instead, or a smaller bundle.
- **Colab "disconnected"** → if the tab idled. Reopen, Run all again (re-upload needed). Keep the tab visible.
- **Free Colab GPU limit hit** → wait a few hours or use Colab Pro / Kaggle (same notebook works).

**What success looks like:** a file `models\hvac_yolov8s_v19.pt` on this PC. Everything after that is automatic.
