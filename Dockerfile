FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# ffmpeg decodes the audio that song recognition fingerprints.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN python -m pip install --no-cache-dir -r requirements.txt

RUN mkdir app
WORKDIR /app
COPY . /app

EXPOSE 8000

# No browser needed — the scraper talks to Shazam's JSON APIs and pages over plain HTTP.
CMD ["python", "run.py"]
