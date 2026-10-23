# CPU worker image (pycolmap from PyPI is CPU-only). The GPU / dense profile needs the COLMAP CUDA
# build and NVIDIA container runtime; it is documented in docs/GPU.md and is not part of this image.
FROM python:3.14-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends libusb-1.0-0 libgomp1 libgl1 libegl1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/vision-worker ./services/vision-worker
RUN pip install --no-cache-dir -e "./services/vision-worker[worker]"
ENV PARALLAXOPS_HOME=/var/lib/parallaxops PYTHONUTF8=1
CMD ["python", "-m", "parallaxops_vision.worker.main"]
