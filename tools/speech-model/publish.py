"""Læg den eksporterede model, hvor serveren leverer den (/models/), og skriv manifestet (speech.json), som appen læser.

Fælles for build.sh og agent.py. Brug: python publish.py <ud-mappe fra export.py> <models-mappen>"""

import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone

src_dir, models = sys.argv[1], sys.argv[2]
src = f"{src_dir}/roest-v3-int8.onnx"
vocab = json.load(open(f"{src_dir}/roest-v3-vocab.json"))
h = hashlib.sha256()
with open(src, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
sha = h.hexdigest()
version = f"roest-v3-int8-{sha[:12]}"
name = f"{version}.onnx"
os.makedirs(models, exist_ok=True)
tmp = f"{models}/{name}.part"
shutil.copyfile(src, tmp)
os.replace(tmp, f"{models}/{name}")
shutil.copyfile(f"{src_dir}/roest-v3-vocab.json", f"{models}/roest-v3-vocab.json")
for old in os.listdir(models):
    if old.startswith("roest-v3-int8-") and old != name:
        os.remove(f"{models}/{old}")
manifest = {"version": version, "url": f"/models/{name}", "size": os.path.getsize(src), "sha256": sha,
            "model": "CoRal-project/roest-v3-wav2vec2-315m", "revision": vocab["revision"], "license": "CoRal (OpenRAIL-M-afledt)",
            "vocab": "/models/roest-v3-vocab.json", "built_at": datetime.now(timezone.utc).isoformat()}
json.dump(manifest, open(f"{models}/speech.json.tmp", "w"), indent=1)
os.replace(f"{models}/speech.json.tmp", f"{models}/speech.json")  # manifestet sidst: først nu tilbydes den nye model
print(json.dumps(manifest, indent=1))
