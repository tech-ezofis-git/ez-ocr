

from __future__ import annotations

import os
import sys
from pathlib import Path
J

HERE = Path(__file__).parent.resolve()
MODELS_DIR = HERE / "models"



os.environ["MODEL_CACHE_DIR"] = str(MODELS_DIR)
os.environ["HF_HOME"] = str(MODELS_DIR / "hf")
os.environ["HF_HUB_CACHE"] = str(MODELS_DIR / "hf" / "hub")
os.environ["TRANSFORMERS_CACHE"] = str(MODELS_DIR / "hf" / "transformers")
os.environ["TORCH_HOME"] = str(MODELS_DIR / "torch")

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "0"

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


for sub in ("hf/hub", "hf/transformers", "torch"):
    (MODELS_DIR / sub).mkdir(parents=True, exist_ok=True)



print(f"Target models dir : {MODELS_DIR}")
print("Downloading Surya predictors (this may take a while)...\n")

from surya.models import load_predictors

try:
    load_predictors()
except Exception as exc:
    print(f"\nDownload failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    sys.exit(1)


total = 0
file_count = 0

for path in MODELS_DIR.rglob("*"):
    if path.is_file():
        total += path.stat().st_size
        file_count += 1

print("\nDone.")
print(f"Files : {file_count}")
print(f"Size  : {total / (1024 ** 3):.2f} GB")
print(f"Path  : {MODELS_DIR}")
print(
    "\nNext step: build the Docker image. "
    "The models folder will be copied in automatically."
)
