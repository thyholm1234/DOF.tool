# Bygger talemodellen til appens tale-indtastning: CoRal-projektets roest-v3-wav2vec2-315m (dansk, CTC) eksporteret til
# ONNX og kvantiseret, så den kan køre på telefonen med ONNX Runtime. Se build.sh (i hånden) og agent.py (service i compose:
# bygger fra Admin → Drift).
FROM python:3.11.10-slim
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 \
 && pip install --no-cache-dir transformers==4.46.3 onnx==1.17.0 onnxruntime==1.20.1 \
      numpy==1.26.4 scipy==1.14.1 soundfile==0.12.1 huggingface_hub==0.26.2 piper-tts==1.2.0 redis==5.2.0
WORKDIR /work
COPY export.py evaluate.py publish.py agent.py /opt/
