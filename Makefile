.PHONY: help install install-dev format lint type check test test-cov coverage clean build docker docker-up docker-down run dev

help:
	@echo "WeatherPi - Development Commands"
	@echo ""
	@echo "Setup:"
	@echo "  make install       - Install production dependencies"
	@echo "  make install-dev   - Install development dependencies"
	@echo ""
	@echo "Code Quality:"
	@echo "  make format        - Format code with black and isort"
	@echo "  make lint          - Run linting checks (ruff, pylint)"
	@echo "  make type          - Type check with mypy"
	@echo "  make check         - Run all quality checks"
	@echo ""
	@echo "Testing:"
	@echo "  make test          - Run tests"
	@echo "  make test-cov      - Run tests with coverage"
	@echo "  make coverage      - Generate coverage report"
	@echo ""
	@echo "Maintenance:"
	@echo "  make clean         - Remove build artifacts and cache"
	@echo ""
	@echo "Building & Running:"
	@echo "  make build         - Build Python package"
	@echo "  make docker        - Build Docker image"
	@echo "  make docker-up     - Start services with docker-compose"
	@echo "  make docker-down   - Stop services"
	@echo "  make run           - Run all services locally"
	@echo "  make dev           - Run with auto-reload for development"

# Setup targets
install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements-dev.txt

# Code quality targets
format:
	black api/ collector/ common/ tests/
	isort api/ collector/ common/ tests/

lint:
	ruff check api/ collector/ common/ tests/ --select E,W,F
	pylint api/ collector/ common/ --exit-zero || true

type:
	mypy api/ collector/ common/ --no-error-summary || true

check: format lint type

# Testing targets
test:
	pytest -v

test-cov:
	pytest -v --cov=api --cov=collector --cov=common --cov-report=html --cov-report=term

coverage: test-cov
	@echo "Coverage report generated in htmlcov/index.html"

# Maintenance targets
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf dist/
	rm -rf build/
	rm -rf *.egg-info

# Build targets
build:
	python setup.py sdist bdist_wheel

docker:
	docker build -t weatherpi:latest .

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

# Running targets
run:
	python -m scripts.run_all

dev:
	python -m uvicorn api.main:app --reload --host 127.0.0.1 --port 8000
