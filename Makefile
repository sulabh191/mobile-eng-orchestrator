.PHONY: help install dev test lint typecheck fmt clean register e2e

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install the orchestrator into the current environment
	python3 -m pip install -e .

dev: ## Install with dev extras
	python3 -m pip install -e ".[dev]"

test: ## Run the test suite
	python3 -m pytest

lint: ## Lint with ruff
	python3 -m ruff check src tests

fmt: ## Auto-format with ruff
	python3 -m ruff check --fix src tests && python3 -m ruff format src tests

typecheck: ## Static type check
	python3 -m mypy

register: ## Register agents/skills/commands with Claude Code and VS Code
	orc install --all

e2e: ## Run the offline end-to-end smoke test
	python3 -m pytest tests/test_end_to_end.py -v

clean:
	rm -rf build dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
