SHELL := /usr/bin/env bash
.ONESHELL:

.PHONY: web

# Run the API and the web dev server together.
web:
	set -euo pipefail
	trap 'kill 0' EXIT
	uv run --group api uvicorn pkmn_quant.api:app --port 8000 &
	(cd web && npm run dev) &
	wait
