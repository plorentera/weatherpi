# Contributing to WeatherPi

Thank you for your interest in contributing to WeatherPi! We welcome contributions from the community. This document provides guidelines and instructions for contributing.

## Code of Conduct

We are committed to providing a welcoming and inclusive environment for all contributors. Please be respectful and constructive in all interactions.

## Getting Started

### 1. Fork and Clone

```bash
git clone https://github.com/plorentera/weatherpi.git
cd weatherpi
```

### 2. Create Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install Development Dependencies

```bash
pip install -r requirements-dev.txt
pip install -e .
```

### 4. Setup Pre-commit Hooks

```bash
pre-commit install
```

## Development Workflow

### Branch Naming

Use descriptive branch names:
- `feature/description` - New features
- `bugfix/description` - Bug fixes
- `docs/description` - Documentation updates
- `refactor/description` - Code refactoring

### Code Style

This project follows these standards:

- **Formatting**: Black (`black --line-length=100`)
- **Import sorting**: isort (`isort --profile=black`)
- **Linting**: ruff
- **Type hints**: mypy
- **Line length**: 100 characters

Format your code before committing:

```bash
black api/ collector/ common/ tests/
isort api/ collector/ common/ tests/
ruff check api/ collector/ common/ tests/
mypy api/ collector/ common/
```

Or use the convenience commands:

```bash
make format
make lint
```

### Testing

All new features must include tests. Run tests with:

```bash
pytest -v --cov
```

Coverage must be maintained above 80%.

### Commit Messages

Use clear, descriptive commit messages:

```
feature: add webhook retry mechanism

- Implemented exponential backoff for failed webhooks
- Added max retry count configuration
- Updated outbox table schema

Fixes #123
```

Format:
- **Type**: `feature`, `bugfix`, `docs`, `refactor`, `test`, `chore`
- **Description**: Clear summary (imperative mood)
- **Body**: Detailed explanation (optional)
- **Footer**: References to issues (optional)

### Pull Requests

1. **Before submitting**: Ensure tests pass and code is formatted
2. **Title**: Clear, descriptive title with type prefix
3. **Description**: Explain what changes and why
4. **Linked issues**: Reference related issues with `Fixes #123`
5. **Breaking changes**: Clearly note any breaking changes

Example:

```markdown
## Description

Adds support for MQTT QoS levels in output configuration.

## Changes

- Extended MQTT config to accept QoS parameter (0, 1, 2)
- Updated database schema
- Added comprehensive tests
- Updated documentation

## Testing

- Unit tests added and passing
- Manual testing on local MQTT broker

Fixes #456
```

## Project Structure

```
weatherpi/
├── api/                 # FastAPI application
├── collector/          # Data collection workers
├── common/             # Shared utilities and models
├── tests/              # Test suite
├── docs/               # Documentation
├── scripts/            # Utility scripts
└── requirements*.txt   # Dependencies
```

## Adding Features

### 1. Model/Schema Changes

If adding new fields or tables:

1. Update `common/models.py` with Pydantic models
2. Update `common/db.py` database schema
3. Create migration if needed
4. Add tests

### 2. API Endpoints

1. Add endpoint to `api/main.py`
2. Use Pydantic models for validation
3. Add docstrings with OpenAPI documentation
4. Add tests to `tests/test_api_*.py`

### 3. Collector Changes

1. Modify `collector/main.py` or add new worker
2. Update configuration in `common/models.py`
3. Add tests

## Documentation

- **Code**: Use docstrings (Google style)
- **README.md**: User-facing documentation
- **API.md**: API reference documentation
- **CONTRIBUTING.md**: This file
- **CHANGELOG.md**: Track user-visible changes

Example docstring:

```python
def process_measurement(timestamp: int, metrics: Dict[str, float]) -> bool:
    """
    Process and store a new measurement.

    Args:
        timestamp: Unix timestamp of measurement
        metrics: Dictionary of measurement values

    Returns:
        True if measurement was stored successfully, False otherwise

    Raises:
        ValueError: If metrics are invalid
    """
```

## Reporting Issues

When reporting issues, include:

- **Python version**: `python --version`
- **OS**: Windows/Linux/macOS
- **Steps to reproduce**: Clear, concise steps
- **Expected behavior**: What should happen
- **Actual behavior**: What actually happened
- **Logs**: Relevant error messages or logs
- **Configuration**: Relevant environment variables or config values

## Questions?

Feel free to:
- Open a GitHub issue for questions
- Discuss in pull request comments
- Create a discussion thread

## License

By contributing, you agree that your contributions will be licensed under the GPL-3.0-or-later license.

Thank you for contributing! 🎉
