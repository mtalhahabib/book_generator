"""Compilation pipeline — Stage 3 of the book generation workflow."""

from __future__ import annotations

import logging

from app.models.enums import BookOutputStatus, ChapterStatus, FinalReviewStatus
from app.services import db_service, export_service, notification_service

logger = logging.getLogger(__name__)


def compile_book(book_id: str, fmt: str = "docx", force: bool = False) -> dict:
    """Compile all approved chapters into a final draft.

    Args:
        book_id: Book UUID.
        fmt: Export format — 'docx', 'pdf', or 'txt'.
        force: If True, skip approval gating and compile all generated chapters.
    """
    book = db_service.get_book(book_id)
    if not book:
        raise ValueError(f"Book {book_id} not found.")

    chapters = db_service.get_chapters_for_book(book_id)

    if not chapters:
        raise ValueError("Cannot compile: No chapters exist for this book. Please generate chapters first.")

    if not force:
        # Verify all chapters are approved
        unapproved = [
            ch for ch in chapters
            if ch.get("status") != ChapterStatus.APPROVED.value
        ]
        if unapproved:
            chapter_nums = [ch["chapter_number"] for ch in unapproved]
            notification_service.notify(
                "error_pause",
                book_id,
                {
                    "title": book["title"],
                    "message": (
                        f"Cannot compile — chapters {chapter_nums} are not approved."
                    ),
                },
            )
            raise ValueError(
                f"Chapters {chapter_nums} are not yet approved. "
                "Approve all chapters first, or use force=true to compile anyway."
            )

        # Check final review gating
        final_status = book.get("final_review_notes_status")
        final_notes  = book.get("final_review_notes")

        if final_status == FinalReviewStatus.YES.value and not final_notes:
            db_service.update_book(book_id, {"book_output_status": BookOutputStatus.PAUSED.value})
            notification_service.notify(
                "error_pause", book_id,
                {"title": book["title"], "message": "Final review notes requested but not provided."},
            )
            raise ValueError("final_review_notes_status is 'yes' but no notes provided.")

        if final_status == FinalReviewStatus.NO.value:
            db_service.update_book(book_id, {"book_output_status": BookOutputStatus.PAUSED.value})
            raise ValueError("final_review_notes_status is 'no' — pipeline paused.")

    # Mark as compiling
    db_service.update_book(book_id, {
        "book_output_status": BookOutputStatus.COMPILING.value,
    })

    # Export
    logger.info("Compiling book %s as %s", book_id, fmt)
    if fmt == "docx":
        file_bytes = export_service.export_docx(book, chapters)
    elif fmt == "pdf":
        file_bytes = export_service.export_pdf(book, chapters)
    elif fmt == "txt":
        txt = export_service.export_txt(book, chapters)
        file_bytes = txt.encode("utf-8")
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    # Upload to storage
    try:
        url = export_service.upload_to_storage(book_id, file_bytes, fmt)
    except Exception as exc:
        logger.warning("Storage upload failed (%s), saving URL as local.", exc)
        url = f"/api/books/{book_id}/download?format={fmt}"

    # Update book status
    updated = db_service.update_book(book_id, {
        "book_output_status": BookOutputStatus.READY.value,
        "output_file_url": url,
    })

    # Notify
    notification_service.notify(
        "final_draft_compiled",
        book_id,
        {
            "title": book["title"],
            "message": f"Final draft compiled as .{fmt}. Ready for download.",
        },
    )

    return updated
