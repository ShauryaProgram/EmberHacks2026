.PHONY: setup build run dev reset-demo test

setup:
	cd backend && $(MAKE) setup
	cd frontend && npm ci

build:
	cd frontend && npm run build

run: build
	PYTHONPATH=backend:. backend/.venv/bin/uvicorn procasination.server:app --host 127.0.0.1 --port 8766

dev:
	@printf '%s\n' 'Run "make run" for the complete app at http://127.0.0.1:8766.'
	@printf '%s\n' 'For frontend hot reload, run "npm run dev --prefix frontend" beside the combined server.'

reset-demo:
	PYTHONPATH=backend:. backend/.venv/bin/python -m procasination.reset_demo

test:
	backend/.venv/bin/pytest -q backend/tests
	PYTHONPATH=backend:. backend/.venv/bin/pytest -q procasination/tests
	cd frontend && npm run build
