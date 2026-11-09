FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    CACHE_PATH=/data/market-cache.json

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY goldsilver ./goldsilver

RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
USER app

# Mount a volume here so a redeploy does not spend API quota refilling the cache.
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import os,urllib.request as u; u.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=2)"

CMD ["python", "-m", "goldsilver"]
