.PHONY: install install-core clean isort lint test

clean:
	find . -name '*.pyc' -exec rm -f {} +
	find . -name '*.pyo' -exec rm -f {} +
	find . -name '__pycache__' -exec rm -rf {} +
	find . -name '.cache' -exec rm -rf {} +
	find . -name '.pytest_cache' -exec rm -rf {} +
	rm -rf build dist *.egg-info .venv

install:
ifndef VIRTUAL_ENV
	$(error Create venv (python -m venv .venv) and activate virtual env first.)
else
	pip install -e .
	pip install pytest
	pip install pylint
	pip install ruff
endif

install-core:
	pip install -e .

isort:
	sh -c "isort --skip-glob=.tox ."

lint:
	pylint --msg-template='{msg_id}({symbol}):{line:3d},{column}: {obj}: {msg}' elkm1_lib
	ruff check

test:
	pytest tests
