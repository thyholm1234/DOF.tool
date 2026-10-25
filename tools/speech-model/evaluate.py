"""Afprøv ONNX-modellerne på danske testsætninger (syntetisk tale fra Piper, dansk stemme): tekst, tid og CTC-log-
sandsynligheder (gemmes som JSON til afprøvning af appens afkoder i web/js/voice-ctc.js).

Brug: python /opt/evaluate.py out/roest-v3-fp32.onnx out/roest-v3-int8.onnx"""

import json
import os
import sys
import time
import wave

import numpy as np
import onnxruntime as ort
from scipy.signal import resample_poly

PHRASES = ["to bomlærker syd", "fem stære trækkende mod vest og tre grågæs rastende", "en rødstrubet lom nord",
           "tolv dobbeltbekkasiner", "atten sanglærker trækkende mod sydvest", "en hvid stork overflyvende",
           "fireogtyve ringduer syd og en spurvehøg", "hundrede og tyve bramgæs rastende", "tre sortgrå ryler"]
OUT = "/work/out"
vocab = json.load(open(f"{OUT}/roest-v3-vocab.json"))
labels, blank, space = vocab["labels"], vocab["blank"], vocab["space"]


def tts(text: str, path: str) -> np.ndarray:
    if not os.path.exists(path):
        os.system(f"echo '{text}' | piper --model /work/voice/da_DK-talesyntese-medium.onnx --output_file {path} 2>/dev/null")
    with wave.open(path) as w:
        sr, a = w.getframerate(), np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return resample_poly(a, 16000, sr).astype(np.float32)


def prep(a: np.ndarray) -> np.ndarray:  # Wav2Vec2FeatureExtractor(do_normalize=True)
    return ((a - a.mean()) / np.sqrt(a.var() + 1e-7))[None, :].astype(np.float32)


def greedy(lp: np.ndarray) -> str:
    out, prev = [], -1
    for i in lp.argmax(-1):
        if i != prev and i != blank:
            out.append(" " if i == space else labels[i])
        prev = i
    return "".join(out).strip()


def log_softmax(x: np.ndarray) -> np.ndarray:
    m = x.max(-1, keepdims=True)
    return x - m - np.log(np.exp(x - m).sum(-1, keepdims=True))


os.makedirs("/work/wav", exist_ok=True)
opts = ort.SessionOptions()
opts.intra_op_num_threads = 4
sessions = {os.path.basename(p): ort.InferenceSession(p, opts, providers=["CPUExecutionProvider"]) for p in sys.argv[1:]}
dump = []
for i, text in enumerate(PHRASES):
    a = tts(text, f"/work/wav/{i}.wav")
    print(f"\n«{text}» ({len(a) / 16000:.1f} s)")
    for name, s in sessions.items():
        t = time.perf_counter()
        lp = log_softmax(s.run(None, {"input_values": prep(a)})[0][0])
        ms = (time.perf_counter() - t) * 1000
        print(f"  {name:22s} {ms:6.0f} ms  {greedy(lp)}")
        if "int8" in name:
            dump.append({"text": text, "frames": lp.shape[0], "logprobs": np.round(lp, 3).tolist()})
json.dump({"labels": labels, "blank": blank, "space": space, "samples": dump}, open(f"{OUT}/samples.json", "w"), ensure_ascii=False)
