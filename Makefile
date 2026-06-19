.PHONY: help install test lint reproduce demo clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Editable install with dev + plotting extras
	pip install -e ".[dev,plot]"

test:  ## Run the test suite
	pytest -q

lint:  ## Type-check with mypy (config in pyproject.toml)
	mypy

reproduce:  ## Download data and regenerate all results (needs network + matplotlib)
	bash scripts/reproduce.sh

demo:  ## Offline demo on the bundled mock fixtures (no data download)
	python -m arbfinder.cli scan
	python -m arbfinder.cli backtest

clean:  ## Remove scratch outputs and caches
	rm -rf results/*.png results/*.json .pytest_cache .mypy_cache
