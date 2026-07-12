FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN git config --global --add safe.directory '*'

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY difflab/ difflab/

# Persistent data volume: SSH keypair and enrolled-machine registry live here.
# Mount a named volume or bind-mount a host directory.
RUN mkdir /data

EXPOSE 8747

# Environment variables:
#   DIFFLAB_DATA   – path to the data directory (default: ./data, use /data with the volume)
#   DIFFLAB_ENROLL_TOKEN – shared secret required to POST /register (enrollment disabled if unset)
#   DIFFLAB_CONFIG – path to config.yaml (default: ./config.yaml)
CMD ["python", "-m", "difflab"]
