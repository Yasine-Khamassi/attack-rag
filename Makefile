# Run from the repo root. `make install` once, then the other targets.
# Uses the project venv if it exists, otherwise whatever `python` is on PATH.
ifeq ($(OS),Windows_NT)
  VENV_PY := .venv/Scripts/python.exe
else
  VENV_PY := .venv/bin/python
endif
PY ?= $(if $(wildcard $(VENV_PY)),$(VENV_PY),python)

.PHONY: install ingest test db

install:            ## create .venv; install package, dev tools and local Postgres (pgserver)
	python -m venv .venv
	$(VENV_PY) -m pip install -e ".[dev,local-db,llm,api]"

ingest:             ## download ATT&CK (cached) and build data/processed/*.jsonl
	$(PY) -m products.attack.ingest

test:               ## run the test suite
	$(PY) -m pytest

db:                 ## start Postgres + pgvector in Docker (alternative: local pgserver, automatic)
	docker compose up -d db
