"""Centralized template loading — Jinja2 for dynamic pages, raw file reads for static pages."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=True,
)


def render(template_name: str, **context) -> str:
    """Render a Jinja2 template with the given context."""
    template = _env.get_template(template_name)
    return template.render(**context)


def read_text(template_name: str) -> str:
    """Read a static HTML file from the templates directory (no Jinja2 processing)."""
    return (_TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
