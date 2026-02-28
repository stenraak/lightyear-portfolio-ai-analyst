"""
Supabase Storage integration for hosting HTML reports publicly.

Setup (one-time in Supabase dashboard):
  Storage → New Bucket → Name: "reports" → Public: ON

The bucket must be public so the URL is accessible without authentication.
Each report is named report_{date}_{time}.html — existing files with the
same name are overwritten so re-runs with --force don't accumulate duplicates.
"""

from pathlib import Path

from src.database.supabase_client import get_client

REPORTS_BUCKET = "reports"


def upload_report(report_path: Path) -> str:
    """
    Upload an HTML report to Supabase Storage.

    Returns the public URL — no authentication required to open it.
    Overwrites any existing file with the same name (idempotent on re-runs).
    """
    client = get_client()
    filename = report_path.name
    html_bytes = report_path.read_bytes()

    # Remove first so we can re-upload without a conflict error
    try:
        client.storage.from_(REPORTS_BUCKET).remove([filename])
    except Exception:
        pass  # File didn't exist yet — fine

    client.storage.from_(REPORTS_BUCKET).upload(
        path=filename,
        file=html_bytes,
        file_options={"content-type": "text/html; charset=utf-8"},
    )

    return client.storage.from_(REPORTS_BUCKET).get_public_url(filename)
