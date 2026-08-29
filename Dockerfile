# Matches the exact Python version and startup command already confirmed
# working on App Service (gunicorn + UvicornWorker, port 8000) -- this
# isn't a new configuration, it's the same one, just running inside a
# container instead of via Kudu's zip-deploy mechanism.
FROM python:3.12-slim

WORKDIR /app

# No system libraries needed here -- libgl1/libglib2.0-0 in the old
# Dockerfile were for opencv/ultralytics (YOLO), which are no longer in
# requirements.txt at all. Leaving them out deliberately, not an
# oversight -- fewer moving parts, smaller image, nothing to go stale.

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

# One CMD. There were two, and only the last counted -- so the first was dead
# text that read like the live configuration, which is worse than no comment.
#
# --timeout 120: a worker is killed if a single request takes longer. Eight
# rotations run concurrently so wall clock is roughly one call, but four
# variants at eight rotations is thirty-two calls in flight and Azure will
# queue some of them. Anything past 120s is a request the platform's own
# 230-second limit would kill shortly afterwards anyway.
CMD ["gunicorn", "-w", "2", "-k", "uvicorn.workers.UvicornWorker", "--timeout", "120", "-b", "0.0.0.0:8000", "app.main:app"]
