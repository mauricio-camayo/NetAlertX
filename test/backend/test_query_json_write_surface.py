"""Regression guards: workflows.json must only be writable through the CSRF-protected endpoint."""

import os
import re

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
QUERY_JSON = os.path.join(REPO_ROOT, "front", "php", "server", "query_json.php")
SESSION_STARTERS = [
    os.path.join(REPO_ROOT, "front", "php", "templates", "security.php"),
    os.path.join(REPO_ROOT, "front", "php", "templates", "auth.php"),
]


def _read(path):
    """Return the text content of a file."""
    with open(path, "r") as f:
        return f.read()


def test_query_json_has_no_write_path():
    """query_json.php is read-only: no POST handler and no file writes."""
    src = _read(QUERY_JSON)
    assert not re.search(r"REQUEST_METHOD'\]\s*===\s*'POST'", src), "POST branch must not exist"
    assert "file_put_contents" not in src, "query_json.php must not write files"
    assert "php://input" not in src, "query_json.php must not read a request body"


def test_query_json_rejects_non_get_methods():
    """Any non-GET method falls through to a 405 response."""
    src = _read(QUERY_JSON)
    assert "http_response_code(405)" in src


def test_session_cookie_sets_samesite():
    """Every file that starts the PHP session must set a SameSite attribute first."""
    for path in SESSION_STARTERS:
        src = _read(path)
        start = src.index("session_start()")
        params = src.find("session_set_cookie_params")
        assert params != -1 and params < start, f"{path}: SameSite must be set before session_start()"
        assert re.search(r"'samesite'\s*=>\s*'(Lax|Strict)'", src), f"{path}: missing SameSite Lax/Strict"
