# 方案 A：同一容器跑 WebUI + CloakBrowser 无头 Chromium。
# 构建：docker compose build
# 启动：docker compose up
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TURB_IN_DOCKER=1 \
    CLOAKBROWSER_CACHE_DIR=/opt/cloakbrowser \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# protocol 模式的 Sentinel runner 会调用 node；Cloak 注册本身不依赖 Node。
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 预拉 Linux Cloak Chromium，避免第一个注册任务才开始下载。
RUN mkdir -p /opt/cloakbrowser \
    && python -m cloakbrowser install \
    && chmod -R a+rX /opt/cloakbrowser

COPY . .

EXPOSE 5000

CMD ["python", "web.py", "--host", "0.0.0.0", "--port", "5000"]
