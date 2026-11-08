FROM python:3.12-slim
WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
RUN python -m py_compile app/main.py app/playback.py app/tablo_live.py app/philo_links.py app/hgtv_fallback.py && python -m unittest discover -s tests -v && rm -rf tests
RUN useradd -r -u 10001 guide && mkdir -p /data && chown guide:guide /data
USER guide
EXPOSE 8787
CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8787"]
