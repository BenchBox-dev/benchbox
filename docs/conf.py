import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(".."))
sys.path.insert(0, os.path.abspath("_static"))
sys.path.insert(0, os.path.abspath("_extensions"))

DOCS_ROOT = Path(__file__).parent

try:
    from pygments import styles
    from pygments_cobalt2 import Cobalt2Style

    styles.STYLE_MAP["cobalt2"] = "pygments_cobalt2::Cobalt2Style"
    import sys
    import types

    cobalt2_module = types.ModuleType("pygments.styles.cobalt2")
    cobalt2_module.Cobalt2Style = Cobalt2Style
    sys.modules["pygments.styles.cobalt2"] = cobalt2_module
except ImportError as e:
    print(f"Warning: Could not import Cobalt2Style: {e}")

try:
    from benchbox import __version__ as _version

    release = _version
except ImportError:
    import re

    _pyproject = DOCS_ROOT.parent / "pyproject.toml"
    if _pyproject.exists():
        _match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', _pyproject.read_text())
        release = _match.group(1) if _match else "0.0.0"
    else:
        release = "0.0.0"

project = "BenchBox"
copyright = "2025, Joe Harris"
author = "Joe Harris"


extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinxcontrib.mermaid",
    "sphinx_tags",
    "sphinx_tags_fix",
    "sphinx_design",
    "ablog",
]

autodoc_mock_imports = [
    "psutil",
    "sqlglot",
    "google.cloud.bigquery",
    "google.cloud.storage",
    "google.api_core",
    "boto3",
    "botocore",
    "snowflake.connector",
    "snowflake.sqlalchemy",
    "databricks.sdk",
    "databricks.sql",
    "clickhouse_driver",
    "redshift_connector",
]


autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": False,
    "exclude-members": "__weakref__",
    "show-inheritance": True,
}

suppress_warnings = [
    "autosummary",
    "ref.myst",
    "myst.xref_missing",
    "toc.not_readable",
    "toc.not_included",
    "app.add_source_parser",
]

autodoc_typehints = "description"
autodoc_typehints_description_target = "documented"

add_module_names = False

pygments_style = "cobalt2"


napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = True
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True
napoleon_use_admonition_for_examples = True
napoleon_use_admonition_for_notes = True
napoleon_use_admonition_for_references = False
napoleon_use_ivar = False
napoleon_use_param = True
napoleon_use_rtype = True
napoleon_type_aliases = None

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]


def _read_path_list(path: Path) -> list[str]:
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


_publish_exclusions = DOCS_ROOT / "publish-exclusions.txt"
if not _publish_exclusions.exists():
    raise FileNotFoundError(f"{_publish_exclusions} is missing; it lists the paths that must not be published")
exclude_patterns += [entry.rstrip("/") for entry in _read_path_list(_publish_exclusions)]

PUBLISH_LIST_ROOTS = ("development", "operations")


def _read_publish_list(docs_root: Path) -> set[str]:
    path = docs_root / "publish-allowlist.txt"
    return set(_read_path_list(path)) if path.exists() else set()


def _unlisted_publish_list_pages(docs_root: Path) -> list[str]:
    listed = _read_publish_list(docs_root)
    pages = (
        page.relative_to(docs_root).as_posix()
        for root in PUBLISH_LIST_ROOTS
        for suffix in ("*.md", "*.rst")
        for page in (docs_root / root).rglob(suffix)
    )
    return sorted(page for page in pages if page not in listed)


exclude_patterns += [page for page in _unlisted_publish_list_pages(DOCS_ROOT) if page not in exclude_patterns]

language = "en"

_linkcheck_ignore_file = DOCS_ROOT / "linkcheck_ignore.txt"
linkcheck_ignore = []
if _linkcheck_ignore_file.exists():
    linkcheck_ignore = [
        line.strip()
        for line in _linkcheck_ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

linkcheck_timeout = 60
linkcheck_retries = 2
linkcheck_report_timeouts_as_broken = True

linkcheck_anchors_ignore_for_url = [
    r"https://docs\.snowflake\.com/.*",
]


html_theme = "furo"
html_static_path = ["_static", "../landing/shared"]

html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    "top_of_page_buttons": ["edit", "view"],
    "light_css_variables": {
        "color-brand-primary": "#2563eb",
        "color-brand-content": "#2563eb",
        "color-highlight-on-target": "#dbeafe",
        "color-background-primary": "#ffffff",
        "color-background-secondary": "#f5f7fb",
        "color-foreground-primary": "#0f172a",
        "color-foreground-secondary": "#475569",
    },
    "dark_css_variables": {
        "color-brand-primary": "#58a6ff",
        "color-brand-content": "#58a6ff",
        "color-highlight-on-target": "#1f6feb",
        "color-background-primary": "#0d1117",
        "color-background-secondary": "#161b22",
        "color-foreground-primary": "#f0f6fc",
        "color-foreground-secondary": "#c9d1d9",
    },
    "source_repository": "https://github.com/BenchBox-dev/benchbox/",
    "source_branch": "main",
    "source_directory": "docs/",
}

html_css_files = [
    "site-header.css",
    "custom.css",
    "site-theme.css",
]

html_js_files = [
    "site-theme.js",
    "site-header.js",
    "collapsible-nav.js",
]


tags_create_tags = True

tags_output_dir = "_tags"

tags_extension = ["md", "rst"]

tags_intro_text = "Tags"

tags_page_title = "Tagged with"

tags_page_header = "Pages with this tag"

tags_index_head = "Documentation Tags"

tags_overview_title = "Tags Overview"

tags_create_badges = True

tags_badge_colors = {
    "beginner": "success",
    "intermediate": "info",
    "advanced": "warning",
    "contributor": "primary",
    "tutorial": "primary",
    "guide": "info",
    "reference": "secondary",
    "concept": "dark",
    "quickstart": "success",
    "tpc-h": "danger",
    "tpc-ds": "danger",
    "tpc-di": "danger",
    "ssb": "warning",
    "clickbench": "warning",
    "h2odb": "warning",
    "custom-benchmark": "secondary",
    "sql-platform": "info",
    "dataframe-platform": "primary",
    "cloud-platform": "success",
    "embedded-platform": "secondary",
    "duckdb": "info",
    "sqlite": "info",
    "snowflake": "success",
    "databricks": "success",
    "bigquery": "success",
    "redshift": "success",
    "clickhouse": "info",
    "polars": "primary",
    "pandas": "primary",
    "cli": "secondary",
    "python-api": "primary",
    "configuration": "info",
    "data-generation": "warning",
    "validation": "info",
    "tuning": "warning",
    "visualization": "primary",
    "cloud-storage": "success",
    "testing": "secondary",
    "performance": "danger",
    "*": "light",
}


skip_injecting_base_ablog_templates = True

blog_title = "BenchBox Blog"
blog_baseurl = "https://benchbox.dev/blog/"
blog_path = "blog"

blog_authors = {
    "Joe Harris": ("Joe Harris", "https://github.com/joeharris76"),
}
blog_default_author = "Joe Harris"

post_date_format = "%B %d, %Y"
post_date_format_short = "%b %d"
post_auto_excerpt = 1
post_show_prev_next = True
post_redirect_refresh = 5

blog_feed_fulltext = True
blog_feed_length = 10

blog_archive_titles = True


html_sidebars = {
    "blog/**": [
        "sidebar/blog-brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "ablog/recentposts.html",
        "ablog/tagcloud.html",
        "ablog/archives.html",
        "sidebar/scroll-end.html",
    ],
}
