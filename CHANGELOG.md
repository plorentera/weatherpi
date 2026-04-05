# Changelog

All notable changes to WeatherPi will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Pydantic models for request/response validation
- Comprehensive test suite with pytest
- pyproject.toml for modern Python packaging
- GitHub Actions CI/CD workflow
- Pre-commit hooks configuration
- Detailed contributing guide
- .env.example for configuration documentation
- .editorconfig for editor consistency
- Development dependencies in requirements-dev.txt
- setup.py for setuptools compatibility
- Type hints documentation
- API docstring examples

### Changed
- Updated requirements.txt with pinned versions
- Improved configuration structure with validation

### Fixed
- Added input validation for API endpoints
- Enhanced configuration validation

### Security
- Strengthened password handling with Pydantic validation
- Added rate limiting guidelines
- Documented security best practices

## [0.1.0] - 2024-01-01

### Added
- Initial project release
- FastAPI-based HTTP API
- SQLite database storage
- Collector process for measurements
- Outputs worker with retry logic
- Backup/export worker
- Web dashboard with Chart.js
- MQTT output support
- Webhook output support
- Authentication and session management
- Configuration management
- Export to CSV functionality

### Features
- RESTful API with FastAPI
- OpenAPI/Swagger documentation
- Session-based authentication
- Role-based access control (reader/admin)
- Sensor abstraction (mock driver)
- Measurement retention policies
- MQTT and webhook outputs
- Scheduled exports
- Responsive web UI

[Unreleased]: https://github.com/plorentera/weatherpi/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/plorentera/weatherpi/releases/tag/v0.1.0
