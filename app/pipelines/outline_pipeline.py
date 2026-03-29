"""Outline pipeline — Stage 1 of the book generation workflow."""

from __future__ import annotations

import logging
import re

from app.models.enums import OutlineStatus
from app.services import db_service, llm_service, notification_service

logger = logging.getLogger(__name__)


def _parse_outline_chapters(outline_text: str) -> list[str]:
    """Extract chapter titles from a Markdown outline.

    Looks for patterns like:
        ## Chapter 1: Title
        **Chapter 1: Title**
        1. Title
    """
    titles: list[str] = []

    # Pattern 1: ## Chapter N: Title
    matches = re.findall(
        r"#{1,3}\s*Chapter\s+\d+[:\.\-–—]\s*(.+)", outline_text, re.IGNORECASE
    )
    if matches:
        titles = [m.strip() for m in matches]
        return titles

    # Pattern 2: **Chapter N: Title**
    matches = re.findall(
        r"\*\*Chapter\s+\d+[:\.\-–—]\s*(.+?)\*\*", outline_text, re.IGNORECASE
    )
    if matches:
        titles = [m.strip() for m in matches]
        return titles

    # Pattern 3: Numbered list  "1. Title"  or  "1. **Title**"
    matches = re.findall(r"^\d+\.\s+\*{0,2}(.+?)\*{0,2}\s*$", outline_text, re.MULTILINE)
    if matches:
        titles = [m.strip() for m in matches]
        return titles

    return titles


def generate_outline(book_id: str) -> dict:
    """Generate (or regenerate) the outline for a book.

    Gating logic:
        - Only runs if notes_on_outline_before exists.
        - After generation, sets status to outline_generated.
        - If notes_on_outline_after exist, incorporates them into regeneration.

    Returns the updated book record.
    """
    book = db_service.get_book(book_id)
    if not book:
        raise ValueError(f"Book {book_id} not found.")

    title = book["title"]
    notes_before = book.get("notes_on_outline_before")
    notes_after = book.get("notes_on_outline_after")

    if not notes_before:
        # Pause — editor must provide pre-outline notes
        notification_service.notify(
            "error_pause",
            book_id,
            {"title": title, "message": "Missing notes_on_outline_before. Please add notes before generating the outline."},
        )
        return db_service.update_book(book_id, {
            "status_outline": OutlineStatus.PENDING.value,
        })

    # Build combined notes
    combined_notes = notes_before
    if notes_after:
        combined_notes += f"\n\nAdditional Editor Notes:\n{notes_after}"

    # Generate via LLM
    logger.info("Generating outline for book %s: %s", book_id, title)
    outline = llm_service.generate_outline(title, combined_notes)

    # Store & update status
    updated = db_service.update_book(book_id, {
        "outline": outline,
        "status_outline": OutlineStatus.OUTLINE_GENERATED.value,
    })

    # Create chapter stubs from outline
    chapter_titles = _parse_outline_chapters(outline)
    if chapter_titles:
        # Clear existing chapters if re-generating
        existing = db_service.get_chapters_for_book(book_id)
        if not existing:
            for i, ch_title in enumerate(chapter_titles, 1):
                db_service.create_chapter(book_id, i, ch_title)

    # Notify editor
    notification_service.notify(
        "outline_ready",
        book_id,
        {"title": title, "message": f"Outline generated with {len(chapter_titles)} chapters."},
    )

    return updated


def approve_outline(
    book_id: str,
    status: OutlineStatus,
    notes_after: str | None = None,
) -> dict:
    """Editor approves the outline or requests changes.

    Args:
        book_id: Book UUID.
        status: New status — 'approved' or 'notes_requested'.
        notes_after: Optional post-outline notes.
    """
    updates: dict = {"status_outline": status.value}
    if notes_after is not None:
        updates["notes_on_outline_after"] = notes_after

    updated = db_service.update_book(book_id, updates)

    if status == OutlineStatus.APPROVED:
        logger.info("Outline approved for book %s", book_id)
    elif status == OutlineStatus.NOTES_REQUESTED:
        logger.info("Outline notes requested for book %s — will regenerate", book_id)

    return updated
