.PHONY: help api-dev app-dev app-install app-build install

help:
	@echo "Targets:"
	@echo "  make install      — install Python deps into .venv (assumes venv exists)"
	@echo "  make app-install  — install Next.js deps under app/"
	@echo "  make api-dev      — run the FastAPI backend on :8000 (when it exists)"
	@echo "  make app-dev      — run the Next.js dev server on :3000"
	@echo "  make app-build    — build the Next.js app for production"

install:
	. .venv/bin/activate && pip install -e ".[dev]"

app-install:
	cd app && pnpm install

api-dev:
	. .venv/bin/activate && uvicorn api.main:app --reload --port 8000

app-dev:
	cd app && pnpm dev

app-build:
	cd app && pnpm build
