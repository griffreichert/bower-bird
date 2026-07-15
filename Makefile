# bower-bird — dev setup & workflow
# `make` or `make help` lists targets. `make setup` does a full fresh setup.

KERNEL_NAME := bower-bird
KERNEL_DISPLAY := bower-bird (uv)

.DEFAULT_GOAL := help

.PHONY: help setup sync hooks kernel lint format test weave sync-vault schedule unschedule clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: sync hooks kernel ## Full setup: env + hooks + jupyter kernel
	@echo "Setup complete. Copy .env.example -> .env and fill in secrets."

sync: ## Build .venv and install deps + dev group
	uv sync

hooks: ## Install pre-commit + commit-msg git hooks
	uv run pre-commit install
	uv run pre-commit install --hook-type commit-msg

kernel: ## Register a Jupyter kernel for this venv
	uv run python -m ipykernel install --user \
		--name $(KERNEL_NAME) --display-name "$(KERNEL_DISPLAY)"

lint: ## Run ruff lint checks
	uv run ruff check .

format: ## Format code with ruff
	uv run ruff format .

test: ## Run unit tests (script-style, no pytest)
	uv run python tests/test_router.py
	uv run python tests/test_ingest.py
	uv run python tests/test_resolve.py
	uv run python tests/test_tweet_docs.py
	uv run python tests/test_vault_docs.py

weave: ## Deep whole-graph synthesis + lint via Claude Code — subscription, manual
	scripts/weave.sh

sync-vault: ## Copy canonical vault docs (vault/*.md) into the owned BowerBird/ folder
	scripts/sync-vault.sh

schedule: ## Install launchd agent: pull every 15 min (override: INTERVAL=<seconds>)
	scripts/install-launchd.sh $(INTERVAL)

unschedule: ## Remove the launchd agent
	scripts/uninstall-launchd.sh

clean: ## Remove venv, caches, and registered kernel
	rm -rf .venv
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .ruff_cache
	-jupyter kernelspec remove -f $(KERNEL_NAME) 2>/dev/null || true
