"""Database service — thin wrapper around the Supabase Python client."""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from supabase import create_client, Client

from app.config import settings
from app.models.enums import BookOutputStatus, ChapterStatus, OutlineStatus

# Monkey-patch supabase-py because v2.11 internally crashes on
# the new 'sb_publishable_' and 'sb_secret_' API keys.
import supabase._sync.client
import supabase._async.client
import re

_orig_match = re.match
def _permissive_match(pattern, string, flags=0):
    if isinstance(string, str) and string.startswith("sb_"):
        class DummyMatch: pass
        return DummyMatch()
    return _orig_match(pattern, string, flags)

supabase._sync.client.re.match = _permissive_match
supabase._async.client.re.match = _permissive_match


logger = logging.getLogger(__name__)

# Module-level cached client (created once, reused across all calls)
_supabase_client: Client | None = None


def _client() -> Client:
    """Return a cached Supabase client (singleton per process)."""
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(settings.supabase_url, settings.supabase_key)
    return _supabase_client


# ── Books ─────────────────────────────────────────────────────────────────────


def create_book(title: str, notes_on_outline_before: Optional[str] = None) -> dict:
    """Insert a new book row and return it."""
    payload: dict[str, Any] = {
        "title": title,
        "status_outline": OutlineStatus.PENDING.value,
        "book_output_status": BookOutputStatus.PENDING.value,
    }
    if notes_on_outline_before:
        payload["notes_on_outline_before"] = notes_on_outline_before
    result = _client().table("books").insert(payload).execute()
    return result.data[0]


def get_book(book_id: str | UUID) -> dict | None:
    """Fetch a single book by ID."""
    result = (
        _client()
        .table("books")
        .select("*")
        .eq("id", str(book_id))
        .execute()
    )
    return result.data[0] if result.data else None


def list_books() -> list[dict]:
    """Return all books ordered by creation date."""
    result = (
        _client()
        .table("books")
        .select("*")
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


def update_book(book_id: str | UUID, updates: dict[str, Any]) -> dict:
    """Patch a book row."""
    result = (
        _client()
        .table("books")
        .update(updates)
        .eq("id", str(book_id))
        .execute()
    )
    return result.data[0]


# ── Chapters ──────────────────────────────────────────────────────────────────


def create_chapter(
    book_id: str | UUID,
    chapter_number: int,
    title: str,
) -> dict:
    """Insert a pending chapter row."""
    payload = {
        "book_id": str(book_id),
        "chapter_number": chapter_number,
        "title": title,
        "status": ChapterStatus.PENDING.value,
    }
    result = _client().table("chapters").insert(payload).execute()
    return result.data[0]


def get_chapter(book_id: str | UUID, chapter_number: int) -> dict | None:
    """Fetch a specific chapter."""
    result = (
        _client()
        .table("chapters")
        .select("*")
        .eq("book_id", str(book_id))
        .eq("chapter_number", chapter_number)
        .execute()
    )
    return result.data[0] if result.data else None


def get_chapters_for_book(book_id: str | UUID) -> list[dict]:
    """Fetch all chapters for a book, ordered by chapter_number."""
    result = (
        _client()
        .table("chapters")
        .select("*")
        .eq("book_id", str(book_id))
        .order("chapter_number")
        .execute()
    )
    return result.data


def update_chapter(chapter_id: str | UUID, updates: dict[str, Any]) -> dict:
    """Patch a chapter row."""
    result = (
        _client()
        .table("chapters")
        .update(updates)
        .eq("id", str(chapter_id))
        .execute()
    )
    return result.data[0]


# ── Sections ──────────────────────────────────────────────────────────────────


def create_sections(chapter_id: str | UUID, section_titles: list[str]) -> list[dict]:
    """Insert multiple pending sections for a chapter."""
    payloads = [
        {
            "chapter_id": str(chapter_id),
            "title": title,
            "status": "pending",
            "order_index": i,
        }
        for i, title in enumerate(section_titles)
    ]
    result = _client().table("sections").insert(payloads).execute()
    return result.data


def get_sections_for_chapter(chapter_id: str | UUID) -> list[dict]:
    """Fetch all sections for a chapter ordered by order_index."""
    result = (
        _client()
        .table("sections")
        .select("*")
        .eq("chapter_id", str(chapter_id))
        .order("order_index")
        .execute()
    )
    return result.data


def update_section(section_id: str | UUID, updates: dict[str, Any]) -> dict:
    """Patch a section row."""
    result = (
        _client()
        .table("sections")
        .update(updates)
        .eq("id", str(section_id))
        .execute()
    )
    return result.data[0]


def delete_sections_for_chapter(chapter_id: str | UUID) -> None:
    """Delete all section rows for a chapter.

    Called before re-saving freshly parsed sections to avoid duplicates
    when a chapter is regenerated.
    """
    _client().table("sections").delete().eq("chapter_id", str(chapter_id)).execute()


# ── Notification Log ──────────────────────────────────────────────────────────


def log_notification(
    book_id: str | UUID,
    event: str,
    channel: str,
    payload: dict,
) -> dict:
    """Persist a notification record."""
    row = {
        "book_id": str(book_id),
        "event": event,
        "channel": channel,
        "payload": payload,
    }
    result = _client().table("notification_log").insert(row).execute()
    return result.data[0]


# ── Supabase Storage ──────────────────────────────────────────────────────────


def upload_file(
    bucket: str,
    path: str,
    file_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload a file to Supabase Storage and return its public URL."""
    client = _client()
    client.storage.from_(bucket).upload(
        path,
        file_bytes,
        {"content-type": content_type},
    )
    public_url = client.storage.from_(bucket).get_public_url(path)
    return public_url
