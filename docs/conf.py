"""Configure Sphinx and initialise Django for API documentation."""

import os
import sys
from pathlib import Path

# Make the project packages importable from the documentation directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Initialise Django before autodoc imports models, forms or views.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

os.environ.setdefault("NEWS_DB_PASSWORD", "documentation-only")

import django

from django.conf import settings

# Autodoc imports models but never reads application data.
settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
django.setup()

project = "News App"
copyright = "2026, Knight Mphande"
author = "Knight Mphande"
release = "1.0.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
]

# Include locally defined classes and functions in generated module pages.
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "alabaster"
html_static_path = ["_static"]
