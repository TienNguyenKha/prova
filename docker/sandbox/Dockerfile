# The image the agent's commands run in. Build:  docker build -t prova-sandbox:1 docker/sandbox
#
# Both inputs are pinned: the base image by digest (not by tag, which can be moved) and the packages by hash.
# To update the packages, edit requirements.in and run:
#   uv pip compile docker/sandbox/requirements.in --generate-hashes --python-version 3.12 --no-header \
#       -o docker/sandbox/requirements.txt
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /tmp/requirements.txt
# --require-hashes: pip refuses any file whose hash is not in the list. --no-deps: the list is already complete.
RUN pip install --require-hashes --no-deps -r /tmp/requirements.txt && rm /tmp/requirements.txt

# The workspace is mounted here when a command runs; nothing else from the host is.
WORKDIR /work
