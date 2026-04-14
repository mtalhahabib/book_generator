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
    """Generate all remaining chapters sequentially."""
    try:
        results = chapter_pipeline.generate_all_chapters(str(book_id))
        return results
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Chapter generation failed: {exc}")


@router.post("/approve-all", response_model=list[ChapterResponse])
def approve_all_chapters(book_id: UUID):
    """Bulk-approve all generated chapters in one call.

    Sets chapter_notes_status='no_notes_needed' and status='approved'
    for every chapter that is currently in 'generated' state.
    After this, POST /api/books/{id}/compile will succeed immediately.
    """
    chapters = db_service.get_chapters_for_book(str(book_id))
    if not chapters:
        raise HTTPException(400, "No chapters found.")

    from app.models.enums import ChapterStatus, NotesStatus
    updated = []
    for ch in chapters:
        if ch.get("status") == ChapterStatus.GENERATED.value:
            result = db_service.update_chapter(ch["id"], {
                "status": ChapterStatus.APPROVED.value,
                "chapter_notes_status": NotesStatus.NO_NOTES_NEEDED.value,
            })
            updated.append(result)
        else:
            updated.append(ch)

    return updated




@router.post("/generate-all-batch")
def generate_all_chapters_batch(book_id: UUID):
    """Submit ALL pending chapters to the Gemini Batch API in one job.

    Unlike /generate-all (sequential, RPM-limited), the Batch API submits
    every chapter simultaneously with no RPM constraints. Results arrive
    asynchronously — poll /batch-status to check progress.

    Returns: { status, job_name, count, chapters }
    """
    try:
        result = chapter_pipeline.generate_all_chapters_batch(str(book_id))
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"Batch submission failed: {exc}")


@router.get("/batch-status")
def get_batch_status(book_id: UUID):
    """Poll the Batch API job for this book and save any completed chapters.

    Call this endpoint periodically after POST /generate-all-batch.
    When done=true, all chapters have been saved to the database.

    Returns: { status, done, saved }
    """
    from app.services import db_service
    book = db_service.get_book(str(book_id))
    if not book:
        raise HTTPException(404, "Book not found.")

    job_name = book.get("batch_job_name")
    if not job_name:
        raise HTTPException(400, "No batch job found for this book. Submit one first via POST /generate-all-batch.")

    try:
        result = chapter_pipeline.process_batch_results(str(book_id), job_name)
        return result
    except Exception as exc:
        raise HTTPException(500, f"Batch polling failed: {exc}")


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
