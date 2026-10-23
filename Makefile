# Cross-platform entry points. On Windows without make, use scripts/dev.ps1 (same targets).
PY ?= python
VISION_DIR := services/vision-worker

.PHONY: test test-unit test-integration lint fmt typecheck

test: lint typecheck test-unit

test-unit:
	cd $(VISION_DIR) && $(PY) -m pytest -m "not integration and not gpu"

# Needs the ETH3D scene downloaded: python scripts/fetch_dataset.py terrace
test-integration:
	cd $(VISION_DIR) && $(PY) -m pytest -m integration

lint:
	cd $(VISION_DIR) && $(PY) -m ruff check . && $(PY) -m ruff format --check .

fmt:
	cd $(VISION_DIR) && $(PY) -m ruff check --fix . && $(PY) -m ruff format .

typecheck:
	cd $(VISION_DIR) && $(PY) -m mypy
