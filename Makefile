.PHONY: install test run demo image

install:
	python -m pip install -e ".[dev]"

test:
	pytest

run:
	delivery-agent

demo:
	./scripts/demo.sh

image:
	docker build -t delivery-agent:local .
