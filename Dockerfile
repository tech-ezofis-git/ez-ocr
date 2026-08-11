FROM mcr.microsoft.com/azure-functions/python:4-python3.11

# =========================================================
# SYSTEM PACKAGES
# =========================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libtesseract-dev \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    fonts-liberation \
    fonts-noto-core \
    fonts-noto-color-emoji \
    fontconfig \
    ca-certificates \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# =========================================================
# WORKDIR
# =========================================================

WORKDIR /home/site/wwwroot

# =========================================================
# COPY FILES
# =========================================================

COPY requirements.txt .

# =========================================================
# PYTHON DEPENDENCIES
# =========================================================

RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir \
        --index-url https://pypi.org/simple \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.7.1+cpu" && \
    pip install --no-cache-dir -r requirements.txt

# =========================================================
# PLAYWRIGHT
# =========================================================

RUN python -m playwright install chromium

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    FUNCTIONS_WORKER_RUNTIME=python \
    PYTHON_ENABLE_WORKER_EXTENSIONS=1 \
    MODEL_CACHE_DIR=/home/site/wwwroot/models \
    HF_HOME=/home/site/wwwroot/models/hf \
    HF_HUB_CACHE=/home/site/wwwroot/models/hf/hub \
    TRANSFORMERS_CACHE=/home/site/wwwroot/models/hf/transformers \
    TORCH_HOME=/home/site/wwwroot/models/torch \
    PADDLE_MODELS_DIR=/home/site/wwwroot/paddle_models \
    TESSERACT_CMD=/usr/bin/tesseract \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1


COPY . .

RUN test -f /home/site/wwwroot/paddle_models/det/inference.yml && \
    test -f /home/site/wwwroot/paddle_models/rec/inference.yml && \
    test -f /home/site/wwwroot/paddle_models/cls/inference.yml

# =========================================================
# EXPOSE
# =========================================================

EXPOSE 80