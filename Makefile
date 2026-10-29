# Run from the repo root. `make install` once, then the other targets.
# Uses the project venv if it exists, otherwise whatever `python` is on PATH.
ifeq ($(OS),Windows_NT)
  VENV_PY := .venv/Scripts/python.exe
else
  VENV_PY := .venv/bin/python
endif
PY ?= $(if $(wildcard $(VENV_PY)),$(VENV_PY),python)

.PHONY: install ingest test

install:            ## create .venv and install the package + dev tools
	python -m venv .venv
	$(VENV_PY) -m pip install -e ".[dev]"

ingest:             ## download ATT&CK (cached) and build data/processed/*.jsonl
	$(PY) -m products.attack.ingest

test:               ## run the test suite
	$(PY) -m pytest
