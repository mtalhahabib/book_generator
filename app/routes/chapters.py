"""Chapter API routes — generate, review, approve chapters."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.models.schemas import ChapterNotesUpdate, ChapterResponse, MessageResponse
from app.pipelines import chapter_pipeline
from app.services import db_service

router = APIRouter(prefix="/api/books/{book_id}/chapters", tags=["Chapters"])


@router.get("", response_model=list[ChapterResponse])
def list_chapters(book_id: UUID):
    """List all chapters for a book, ordered by chapter number."""
    chapters = db_service.get_chapters_for_book(str(book_id))
    return chapters


@router.get("/{chapter_number}", response_model=ChapterResponse)
def get_chapter(book_id: UUID, chapter_number: int):
    """Get a single chapter by its number."""
    chapter = db_service.get_chapter(str(book_id), chapter_number)
    if not chapter:
        raise HTTPException(404, "Chapter not found.")
    return chapter


@router.post("/generate", response_model=ChapterResponse)
def generate_next_chapter(book_id: UUID):
    """Generate the next pending chapter.

    Finds the first chapter with status 'pending' and generates it
    using context from all previous chapter summaries.
    """
    chapters = db_service.get_chapters_for_book(str(book_id))
    if not chapters:
        raise HTTPException(400, "No chapters found. Generate outline first.")

    # Find first pending chapter
    target = None
    for ch in chapters:
        if ch.get("status") in ("pending", None):
            target = ch
            break

    if not target:
        raise HTTPException(400, "No pending chapters to generate.")

    try:
        updated = chapter_pipeline.generate_chapter(
            str(book_id), target["chapter_number"]
        )
        return updated
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Chapter generation failed: {exc}")


@router.post("/generate/{chapter_number}", response_model=ChapterResponse)
def generate_specific_chapter(book_id: UUID, chapter_number: int):
    """Generate (or regenerate) a specific chapter by number."""
    try:
        updated = chapter_pipeline.generate_chapter(
            str(book_id), chapter_number
        )
        return updated
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Chapter generation failed: {exc}")


@router.post("/generate-all", response_model=list[ChapterResponse])
def generate_all_chapters(book_id: UUID):
    """Generate all remaining chapters sequentially.

    Respects gating:
    - Chapters with chapter_notes_status='yes' will be skipped (waiting).
    - Already approved chapters are skipped.
    """
    try:
        results = chapter_pipeline.generate_all_chapters(str(book_id))
        return results
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Chapter generation failed: {exc}")


@router.patch("/{chapter_number}", response_model=ChapterResponse)
def update_chapter_notes(
    book_id: UUID, chapter_number: int, body: ChapterNotesUpdate
):
    """Add notes to a chapter or approve it.

    After adding notes, call generate/{chapter_number} to regenerate.
    Setting chapter_notes_status to 'no_notes_needed' auto-approves.
    """
    try:
        updated = chapter_pipeline.update_chapter_notes(
            book_id=str(book_id),
            chapter_number=chapter_number,
            notes=body.chapter_notes,
            notes_status=body.chapter_notes_status,
        )
        return updated
    except ValueError as exc:
        raise HTTPException(400, str(exc))
