"""Fælles testopsætning: mapper til medier og talemodel i en midlertidig mappe (main.py opretter dem ved import)."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="doftool-test-")
os.environ.setdefault("MEDIA_DIR", f"{_tmp}/media")
os.environ.setdefault("MODELS_DIR", f"{_tmp}/models")
