# Convenience targets mirroring .github/workflows/ci.yml.
PY := .venv/bin/python

.PHONY: setup build test conformance demo ci clean

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt
	cd typescript && npm ci

build:
	cd typescript && npm run typecheck && npm run build

test: build
	cd typescript && npm test
	$(PY) -m pytest -q

conformance: build
	$(PY) conformance/run.py

demo: build
	.venv/bin/hitl-agent --server python --auto-reject --fixed-clock 2026-03-02T09:00:00Z
	.venv/bin/hitl-agent --server typescript --auto-reject --fixed-clock 2026-03-02T09:00:00Z

ci: test conformance demo

clean:
	rm -rf typescript/dist .pytest_cache .mypy_cache .ruff_cache
