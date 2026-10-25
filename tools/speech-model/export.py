"""Eksportér roest-v3-wav2vec2-315m til ONNX (fp32) og lav en int8-udgave til telefonen.

Kvantiseringen er dynamisk og rammer kun MatMul/Gemm (transformer-lagene); conv-lagene i lydindgangen bliver i fp32,
fordi int8 dér koster tydeligt på genkendelsen af wav2vec2. Ud: /work/out/roest-v3-fp32.onnx, roest-v3-int8.onnx,
roest-v3-vocab.json (index → tegn; "|" = mellemrum, <pad> = CTC-blank)."""

import json
import os

import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import Wav2Vec2ForCTC

REPO = "CoRal-project/roest-v3-wav2vec2-315m"
REV = os.environ.get("ROEST_REVISION", "beb3e790246d6b9dec1df596b0b21d5c42f4d99c")
OUT = "/work/out"
os.makedirs(OUT, exist_ok=True)

model = Wav2Vec2ForCTC.from_pretrained(REPO, revision=REV).eval()
dummy = torch.zeros(1, 16000 * 3)
fp32 = f"{OUT}/roest-v3-fp32.onnx"
torch.onnx.export(model, (dummy,), fp32, input_names=["input_values"], output_names=["logits"],
                  dynamic_axes={"input_values": {1: "samples"}, "logits": {1: "frames"}}, opset_version=17,
                  do_constant_folding=True, dynamo=False)
quantize_dynamic(fp32, f"{OUT}/roest-v3-int8.onnx", weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul", "Gemm"])

from huggingface_hub import hf_hub_download  # noqa: E402

vocab = json.load(open(hf_hub_download(REPO, "vocab.json", revision=REV)))
added = json.load(open(hf_hub_download(REPO, "added_tokens.json", revision=REV)))
labels = {**vocab, **added}
json.dump({"labels": [k for k, _ in sorted(labels.items(), key=lambda kv: kv[1])], "blank": model.config.pad_token_id,
           "space": vocab["|"], "revision": REV}, open(f"{OUT}/roest-v3-vocab.json", "w"), ensure_ascii=False)
for f in sorted(os.listdir(OUT)):
    print(f, round(os.path.getsize(f"{OUT}/{f}") / 1e6), "MB")
