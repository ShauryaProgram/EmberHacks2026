.PHONY: setup run test onboard status doctor sync

PYTHON ?= python3.11

setup:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements-dev.txt

run:
	.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8766

test:
	.venv/bin/pytest -q

onboard:
	.venv/bin/python -m app.cli onboard

status:
	.venv/bin/python -m app.cli status

doctor:
	.venv/bin/python -m app.cli doctor

sync:
	.venv/bin/python -m app.cli sync
