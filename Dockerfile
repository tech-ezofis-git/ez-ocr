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

# paddlepaddle==2.6.1 is not reliably on PyPI — install from Paddle CPU index,
# then install the rest of requirements without re-resolving paddlepaddle.
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir \
        --index-url https://pypi.org/simple \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==2.7.1+cpu" && \
    pip install --no-cache-dir "numpy<2" && \
    pip install --no-cache-dir "paddlepaddle==2.6.1" \
        -i https://www.paddlepaddle.org.cn/packages/stable/cpu/ && \
    grep -v -E '^[[:space:]]*paddlepaddle==' requirements.txt \
        > /tmp/requirements.docker.txt && \
    pip install --no-cache-dir -r /tmp/requirements.docker.txt && \
    pip install --no-cache-dir "protobuf<=3.20.2"

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
    PADDLE_MODELS_DIR=/home/site/wwwroot/paddle \
    PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
    TESSERACT_CMD=/usr/bin/tesseract \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1


COPY . .

RUN test -d /home/site/wwwroot/paddle/det && \
    test -d /home/site/wwwroot/paddle/rec && \
    test -d /home/site/wwwroot/paddle/cls

# =========================================================
# EXPOSE
# =========================================================

EXPOSE 80