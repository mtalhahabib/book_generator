"""Chapter pipeline — Stage 2 of the book generation workflow."""

from __future__ import annotations

import logging

from app.models.enums import ChapterStatus, NotesStatus
from app.services import db_service, llm_service, notification_service

logger = logging.getLogger(__name__)


def generate_chapter(book_id: str, chapter_number: int) -> dict:
    """Generate (or regenerate) a single chapter.

    Context chaining:
        Before writing chapter N, summaries of chapters 1..N-1 are
        gathered and passed as context to the LLM.

    Gating logic:
        - After generation, sets chapter status to 'generated'.
        - Sends notification for editor review.

    Returns the updated chapter record.
    """
    book = db_service.get_book(book_id)
    if not book:
        raise ValueError(f"Book {book_id} not found.")
    if not book.get("outline"):
        raise ValueError(f"Book {book_id} has no outline. Generate outline first.")

    chapters = db_service.get_chapters_for_book(book_id)
    chapter = None
    for ch in chapters:
        if ch["chapter_number"] == chapter_number:
            chapter = ch
            break

    if not chapter:
        raise ValueError(
            f"Chapter {chapter_number} not found for book {book_id}."
        )

    total_chapters = len(chapters)

    # Gather previous chapter summaries for context chaining
    previous_summaries: list[str] = []
    for ch in chapters:
        if ch["chapter_number"] < chapter_number and ch.get("summary"):
            previous_summaries.append(ch["summary"])

    # Mark as generating
    db_service.update_chapter(chapter["id"], {
        "status": ChapterStatus.GENERATING.value,
    })

    # Generate via LLM
    logger.info(
        "Generating chapter %d/%d for book %s",
        chapter_number, total_chapters, book_id,
    )
    content = llm_service.generate_chapter(
        book_title=book["title"],
        outline=book["outline"],
        chapter_title=chapter.get("title", f"Chapter {chapter_number}"),
        chapter_number=chapter_number,
        total_chapters=total_chapters,
        previous_summaries=previous_summaries,
        chapter_notes=chapter.get("chapter_notes"),
    )

    # Summarize for future context chaining
    summary = llm_service.summarize_chapter(content)

    # Store results
    updated = db_service.update_chapter(chapter["id"], {
        "content": content,
        "summary": summary,
        "status": ChapterStatus.GENERATED.value,
    })

    # Notify editor
    notification_service.notify(
        "chapter_ready",
        book_id,
        {
            "title": book["title"],
            "chapter_number": chapter_number,
            "message": f"Chapter {chapter_number}: {chapter.get('title', '')} is ready for review.",
        },
    )

    return updated


def generate_all_chapters(book_id: str) -> list[dict]:
    """Generate all pending chapters sequentially with context chaining.

    For each chapter:
        1. Check chapter_notes_status gating.
        2. Generate if allowed.
        3. Move to next chapter.

    Returns list of generated/updated chapter records.
    """
    book = db_service.get_book(book_id)
    if not book:
        raise ValueError(f"Book {book_id} not found.")

    chapters = db_service.get_chapters_for_book(book_id)
    results: list[dict] = []

    for ch in chapters:
        status = ch.get("status", ChapterStatus.PENDING.value)
        notes_status = ch.get("chapter_notes_status")

        # Skip already-approved chapters
        if status == ChapterStatus.APPROVED.value:
            results.append(ch)
            continue

        # Gating logic
        if notes_status == NotesStatus.YES.value:
            # Wait for notes — skip this chapter
            notification_service.notify(
                "waiting_chapter_notes",
                book_id,
                {
                    "title": book["title"],
                    "chapter_number": ch["chapter_number"],
                    "message": f"Waiting for notes on Chapter {ch['chapter_number']}.",
                },
            )
            results.append(ch)
            continue
        elif notes_status == NotesStatus.NO.value or (
            notes_status is None and status == ChapterStatus.GENERATED.value
        ):
            # Paused — no/empty status on a generated chapter
            results.append(ch)
            continue

        # Generate this chapter
        updated = generate_chapter(book_id, ch["chapter_number"])
        results.append(updated)

    return results


def update_chapter_notes(
    book_id: str,
    chapter_number: int,
    notes: str | None,
    notes_status: NotesStatus,
) -> dict:
    """Editor adds notes or approves a chapter.

    Args:
        book_id: Book UUID.
        chapter_number: Chapter number.
        notes: Optional chapter-specific notes.
        notes_status: 'yes' (notes added), 'no', or 'no_notes_needed'.
    """
    chapters = db_service.get_chapters_for_book(book_id)
    chapter = None
    for ch in chapters:
        if ch["chapter_number"] == chapter_number:
            chapter = ch
            break

    if not chapter:
        raise ValueError(f"Chapter {chapter_number} not found for book {book_id}.")

    updates: dict = {"chapter_notes_status": notes_status.value}
    if notes is not None:
        updates["chapter_notes"] = notes

    # If no_notes_needed, auto-approve
    if notes_status == NotesStatus.NO_NOTES_NEEDED:
        updates["status"] = ChapterStatus.APPROVED.value

    return db_service.update_chapter(chapter["id"], updates)
