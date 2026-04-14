"""Chapter pipeline — Stage 2 of the book generation workflow.

v3 architecture: ONE API call per chapter (down from 7).

Call sequence per chapter:
  1. llm_service.generate_chapter()   → full chapter markdown (1 API call)
  2. llm_service.parse_sections_from_chapter()  → parse ## headers (free)
  3. db_service.create_sections()     → save parsed sections to DB (free)
  4. llm_service.extract_chapter_snippet() → 800-char context snippet (free)
     ↑ replaces old summarize_chapter() which cost 1 extra API call

Total: 1 API call per chapter  (was 7: titles + 5 sections + summary)
"""

from __future__ import annotations

import logging
import threading
import time

from app.models.enums import ChapterStatus, NotesStatus
from app.services import db_service, llm_service, notification_service

logger = logging.getLogger(__name__)


def generate_chapter(book_id: str, chapter_number: int) -> dict:
    """Generate (or regenerate) a chapter in a single LLM call.

    Flow:
      1. Fetch book + chapter metadata from DB.
      2. Call generate_chapter() → full chapter markdown.
      3. Parse ## sections out of the markdown.
      4. Persist sections + assembled content to DB.
      5. Extract 800-char snippet for next chapter's context.
    """
    book = db_service.get_book(book_id)
    if not book:
        raise ValueError(f"Book {book_id} not found.")
    if not book.get("outline"):
        raise ValueError(f"Book {book_id} has no outline. Generate outline first.")

    chapters = db_service.get_chapters_for_book(book_id)
    chapter  = next((ch for ch in chapters if ch["chapter_number"] == chapter_number), None)
    if not chapter:
        raise ValueError(f"Chapter {chapter_number} not found for book {book_id}.")

    chapter_id    = chapter["id"]
    chapter_title = chapter.get("title", f"Chapter {chapter_number}")

    # Mark as generating
    db_service.update_chapter(chapter_id, {"status": ChapterStatus.GENERATING.value})

    # ── Build context from previous chapter (snippet, not LLM summary) ──────
    previous_snippet = None
    if chapter_number > 1:
        prev_ch = next(
            (ch for ch in chapters if ch["chapter_number"] == chapter_number - 1),
            None,
        )
        if prev_ch and prev_ch.get("content"):
            previous_snippet = llm_service.extract_chapter_snippet(
                prev_ch["content"], max_chars=800
            )

    # ── ONE API call: generate full chapter ──────────────────────────────────
    raw_chapter = llm_service.generate_chapter(
        book_title=book["title"],
        chapter_number=chapter_number,
        chapter_title=chapter_title,
        outline=book["outline"],
        previous_chapter_snippet=previous_snippet,
        chapter_notes=chapter.get("chapter_notes"),
    )

    # ── Parse ## sections from the markdown response ──────────────────────────
    parsed_sections = llm_service.parse_sections_from_chapter(raw_chapter)
    logger.info(
        "Chapter %d: parsed %d sections from one-call response.",
        chapter_number, len(parsed_sections),
    )

    # ── Persist sections ──────────────────────────────────────────────────────
    # Delete stale sections (from any previous generation attempt)
    db_service.delete_sections_for_chapter(chapter_id)

    # Create new sections from parsed content
    section_titles = [s["title"] for s in parsed_sections]
    db_service.create_sections(chapter_id, section_titles)
    # Fetch the created section records to get their IDs
    sections = db_service.get_sections_for_chapter(chapter_id)

    # Write content into each section record
    for sec_record, parsed in zip(sections, parsed_sections):
        db_service.update_section(sec_record["id"], {
            "content": parsed["content"],
            "status":  "done",
        })

    # ── Assemble full chapter markdown ────────────────────────────────────────
    assembled = f"# {chapter_title}\n\n"
    for sec in parsed_sections:
        assembled += f"## {sec['title']}\n\n{sec['content']}\n\n"

    # ── Save chapter (no summarize API call — snippet is free) ───────────────
    updated = db_service.update_chapter(chapter_id, {
        "content": assembled,
        # summary field gets the first 800 chars — no extra API call needed
        "summary": llm_service.extract_chapter_snippet(assembled, max_chars=800),
        "status":  ChapterStatus.GENERATED.value,
    })

    # Notify editor
    notification_service.notify(
        "chapter_ready",
        book_id,
        {
            "title":          book["title"],
            "chapter_number": chapter_number,
            "message":        f"Chapter {chapter_number}: {chapter_title} is ready for review.",
        },
    )

    logger.info(
        "Chapter %d done. 1 API call used (down from 7). %d sections saved.",
        chapter_number, len(parsed_sections),
    )
    return updated


def generate_all_chapters(book_id: str) -> list[dict]:
    """Generate all pending chapters sequentially with context chaining.

    Uses gating logic:
    - Chapters with chapter_notes_status='yes' are skipped (waiting for notes).
    - Already approved chapters are skipped.
    """
    book = db_service.get_book(book_id)
    if not book:
        raise ValueError(f"Book {book_id} not found.")

    chapters = db_service.get_chapters_for_book(book_id)
    results: list[dict] = []

    for ch in chapters:
        status       = ch.get("status", ChapterStatus.PENDING.value)
        notes_status = ch.get("chapter_notes_status")

        if status == ChapterStatus.APPROVED.value:
            results.append(ch)
            continue

        if notes_status == NotesStatus.YES.value:
            notification_service.notify(
                "waiting_chapter_notes",
                book_id,
                {
                    "title":          book["title"],
                    "chapter_number": ch["chapter_number"],
                    "message":        f"Waiting for notes on Chapter {ch['chapter_number']}.",
                },
            )
            results.append(ch)
            continue
        elif notes_status == NotesStatus.NO.value or (
            notes_status is None and status == ChapterStatus.GENERATED.value
        ):
            results.append(ch)
            continue

        updated = generate_chapter(book_id, ch["chapter_number"])
        results.append(updated)

    return results


def generate_all_chapters_batch(book_id: str) -> dict:
    """Submit all pending chapters as a single Gemini Batch API job.

    Unlike generate_all_chapters() which is sequential and subject to RPM
    limits, this submits ALL chapters simultaneously as one batch job.

    The Batch API has SEPARATE rate limits (100 concurrent jobs, no RPM cap).
    Typical turnaround: a few minutes. Google SLA: up to 24 hours.

    Returns batch job metadata for polling via GET /api/books/{id}/batch-status.
    """
    book = db_service.get_book(book_id)
    if not book:
        raise ValueError(f"Book {book_id} not found.")
    if not book.get("outline"):
        raise ValueError(f"Book {book_id} has no outline.")

    chapters = db_service.get_chapters_for_book(book_id)
    pending = [
        ch for ch in chapters
        if ch.get("status") not in (
            ChapterStatus.APPROVED.value, ChapterStatus.GENERATED.value
        )
        and ch.get("chapter_notes_status") != NotesStatus.YES.value
    ]

    if not pending:
        return {"status": "no_pending_chapters", "job_name": None, "count": 0}

    # Build batch requests — all chapters simultaneously
    requests = llm_service.build_chapter_batch_requests(
        book_title=book["title"],
        chapters=pending,
        outline=book["outline"],
    )

    # Mark all pending chapters as GENERATING
    for ch in pending:
        db_service.update_chapter(ch["id"], {"status": ChapterStatus.GENERATING.value})

    # Submit to Gemini Batch API
    job_name = llm_service.submit_batch_job(
        requests=requests,
        display_name=f"book-{book_id[:8]}-all-chapters",
    )

    # Persist job_name in book record so we can poll later
    db_service.update_book(book_id, {"batch_job_name": job_name})

    logger.info(
        "Batch job submitted for book '%s': %d chapters. Job: %s",
        book["title"], len(pending), job_name,
    )
    return {
        "status":    "submitted",
        "job_name":  job_name,
        "count":     len(pending),
        "chapters":  [ch["chapter_number"] for ch in pending],
    }


def process_batch_results(book_id: str, job_name: str) -> dict:
    """Poll a batch job and persist completed chapters to DB.

    Call this from GET /api/books/{id}/batch-status.
    When the job is done, parses each chapter response and saves to DB
    exactly as generate_chapter() would.
    """
    poll = llm_service.poll_batch_job(job_name)

    if not poll["done"]:
        return {"status": poll["state"], "done": False, "saved": 0}

    if poll["state"] != "JOB_STATE_SUCCEEDED":
        return {"status": poll["state"], "done": True, "saved": 0, "error": "Batch job did not succeed."}

    responses = poll["responses"]  # { "chapter-N": "<markdown>" }
    chapters  = db_service.get_chapters_for_book(book_id)
    book      = db_service.get_book(book_id)
    saved     = 0

    for key, raw_chapter in responses.items():
        # key format: "chapter-N"
        try:
            ch_num = int(key.split("-")[1])
        except (IndexError, ValueError):
            logger.warning("Unknown batch response key: %s", key)
            continue

        chapter = next((ch for ch in chapters if ch["chapter_number"] == ch_num), None)
        if not chapter:
            logger.warning("No DB chapter found for batch key %s", key)
            continue

        chapter_id    = chapter["id"]
        chapter_title = chapter.get("title", f"Chapter {ch_num}")

        parsed_sections = llm_service.parse_sections_from_chapter(raw_chapter)

        # Persist sections
        db_service.delete_sections_for_chapter(chapter_id)
        section_titles = [s["title"] for s in parsed_sections]
        db_service.create_sections(chapter_id, section_titles)
        sections = db_service.get_sections_for_chapter(chapter_id)

        for sec_record, parsed in zip(sections, parsed_sections):
            db_service.update_section(sec_record["id"], {
                "content": parsed["content"],
                "status":  "done",
            })

        assembled = f"# {chapter_title}\n\n"
        for sec in parsed_sections:
            assembled += f"## {sec['title']}\n\n{sec['content']}\n\n"

        db_service.update_chapter(chapter_id, {
            "content": assembled,
            "summary": llm_service.extract_chapter_snippet(assembled),
            "status":  ChapterStatus.GENERATED.value,
        })

        notification_service.notify(
            "chapter_ready",
            book_id,
            {
                "title":          book["title"],
                "chapter_number": ch_num,
                "message":        f"Chapter {ch_num}: {chapter_title} ready (batch).",
            },
        )
        saved += 1

    logger.info("Batch results saved: %d/%d chapters.", saved, len(responses))
    return {"status": "JOB_STATE_SUCCEEDED", "done": True, "saved": saved}


def update_chapter_notes(
    book_id: str,
    chapter_number: int,
    notes: str | None,
    notes_status: NotesStatus,
) -> dict:
    """Editor adds notes or approves a chapter."""
    chapters = db_service.get_chapters_for_book(book_id)
    chapter  = next(
        (ch for ch in chapters if ch["chapter_number"] == chapter_number), None
    )
    if not chapter:
        raise ValueError(f"Chapter {chapter_number} not found for book {book_id}.")

    updates: dict = {"chapter_notes_status": notes_status.value}
    if notes is not None:
        updates["chapter_notes"] = notes

    if notes_status == NotesStatus.NO_NOTES_NEEDED:
        updates["status"] = ChapterStatus.APPROVED.value

    return db_service.update_chapter(chapter["id"], updates)
