# Contributing to PVE2Services

Thank you for your interest in contributing to PVE2Services! This document provides guidelines and information for contributors.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Code Style](#code-style)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Plugin Development](#plugin-development)
- [Reporting Issues](#reporting-issues)

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/PVE2Services.git
   cd PVE2Services
   ```
3. Create a branch for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

### Prerequisites

- Python 3.11 or higher
- pip
- Git

### Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
pip install -e ".[test]"

# Run the application
uvicorn PVE2Services.core.wiki_service.app.main:app --reload --port 8000
```

### Docker Development

```bash
docker compose up -d
# Visit http://localhost:8000/admin/login
```

For PostgreSQL:
```bash
PVE2_DB_MODE=postgres_docker docker compose up -d
```

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting with the following configuration:

- Line length: 100 characters
- Target Python version: 3.11+
- Lint rules: E (pycodestyle), F (pyflakes), I (isort), B (flake8-bugbear)

Run linting:
```bash
ruff check .
```

Auto-fix issues:
```bash
ruff check --fix .
```

### Code Conventions

- Use type hints for function signatures
- Follow PEP 8 naming conventions
- Keep functions focused and small
- Use descriptive variable names
- Add docstrings for public functions and classes

## Testing

We use [pytest](https://pytest.org/) for testing.

### Running Tests

```bash
# Run all tests
pytest -q tests

# Run with verbose output
pytest -v tests

# Stop on first failure
pytest -q tests -x

# Run specific test file
pytest -q tests/test_auth.py

# Run specific test
pytest -q tests/test_auth.py::test_login_success
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files `test_*.py`
- Name test functions `test_*`
- Use fixtures for common setup (see `conftest.py`)
- Mock external services (Proxmox API, Wiki.js, etc.)

### Test Categories

- **Unit tests:** Test individual functions and classes
- **Integration tests:** Test API endpoints and database operations
- **Plugin tests:** Test specific plugin functionality

## Pull Request Process

### Before Submitting

1. Ensure all tests pass: `pytest -q tests`
2. Run linting: `ruff check .`
3. Update documentation if needed
4. Add tests for new functionality

### PR Guidelines

- **Keep PRs focused:** One feature or fix per PR
- **Write clear titles:** Describe what the PR does
- **Add description:** Explain the changes and why they're needed
- **Reference issues:** Link related issues (e.g., "Fixes #123")
- **Include tests:** Add tests for new features
- **Update docs:** Modify documentation if behavior changes

### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Tests pass locally
- [ ] New tests added (if applicable)

## Checklist
- [ ] Code follows project style
- [ ] Self-reviewed code
- [ ] Comments added for complex code
- [ ] Documentation updated
- [ ] No new warnings
```

## Plugin Development

PVE2Services uses a plugin architecture. To add a new plugin:

### Quick Start

1. Create a directory under `plugins/` (e.g., `plugins/my_plugin/`)
2. Add `__init__.py` with a `load_plugin(app)` function
3. Restart the application — your plugin is auto-discovered!

### Resources

- **[Plugin Development Guide](PLUGIN_DEVELOPMENT.md)** — Complete guide with examples
- **[Example Plugin](plugins/example_plugin/)** — Working demo plugin
- **Existing plugins** — See `plugins/PVE2DNS/`, `plugins/PVE2Power/`, etc.

### Plugin Structure

```
plugins/my_plugin/
    __init__.py      # Required: load_plugin(app) function
    schemas.py       # Pydantic models for configuration
    frontend.py      # HTML admin page builder
    providers.py     # Backend integrations (optional)
```

### Standard Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/plugins/<id>/info` | Plugin metadata |
| GET | `/api/plugins/<id>/config` | Read configuration |
| POST | `/api/plugins/<id>/config` | Save configuration |
| POST | `/api/plugins/<id>/test` | Test connectivity |
| POST | `/api/plugins/<id>/sync` | Trigger sync |
| GET | `/admin/<id>` | Admin HTML page |

### Minimal Example

```python
from fastapi import FastAPI

def load_plugin(app: FastAPI):
    @app.get("/api/plugins/myplugin/info")
    async def info():
        return {
            "plugin": "myplugin",
            "name": "My Plugin",
            "version": "0.1.0",
            "description": "A custom plugin",
            "status": "active",
        }
```

### Using Shared Libraries

```python
from PVE2Services.libs.db_adapter import DBAdapter, get_db
from PVE2Services.libs.connector_config import ConnectorConfig, get_config_manager
from PVE2Services.libs.errors import PluginError, ConfigError
```

## Reporting Issues

### Bug Reports

When reporting bugs, please include:

- **Environment:** OS, Python version, Docker (if applicable)
- **Steps to reproduce:** Clear steps to trigger the issue
- **Expected behavior:** What should happen
- **Actual behavior:** What actually happens
- **Logs:** Relevant error messages or logs
- **Screenshots:** If applicable

### Feature Requests

We welcome feature requests! Please:

- Check if the feature already exists or is planned
- Describe the use case and why it's valuable
- Consider implementation complexity
- Be open to discussion and alternatives

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inclusive experience for everyone. We pledge to act and interact in ways that contribute to an open, friendly, diverse, and healthy community.

### Our Standards

Expected behavior includes:

- Using welcoming and inclusive language
- Being respectful of differing viewpoints
- Gracefully accepting constructive criticism
- Focusing on what is best for the community
- Showing empathy towards other community members

Unacceptable behavior includes:

- Trolling, insulting/derogatory comments, and personal attacks
- Public or private harassment
- Publishing others' private information without permission
- Other conduct which could reasonably be considered inappropriate

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

## Questions?

If you have questions about contributing, feel free to open an issue or reach out to the maintainers.
