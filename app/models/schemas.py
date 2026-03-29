"""Pydantic schemas for API request/response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    BookOutputStatus,
    ChapterStatus,
    FinalReviewStatus,
    NotesStatus,
    OutlineStatus,
)


# ── Book Schemas ──────────────────────────────────────────────────────────────


class BookCreate(BaseModel):
    """Payload to create a new book."""
    title: str = Field(..., min_length=1, description="Book title (mandatory)")
    notes_on_outline_before: Optional[str] = Field(
        None, description="Notes to guide outline generation"
    )


class BookUpdate(BaseModel):
    """Payload to update book-level fields."""
    notes_on_outline_before: Optional[str] = None
    notes_on_outline_after: Optional[str] = None
    status_outline: Optional[OutlineStatus] = None
    final_review_notes: Optional[str] = None
    final_review_notes_status: Optional[FinalReviewStatus] = None


class BookResponse(BaseModel):
    """Full book record returned from the API."""
    id: UUID
    title: str
    notes_on_outline_before: Optional[str] = None
    outline: Optional[str] = None
    notes_on_outline_after: Optional[str] = None
    status_outline: OutlineStatus = OutlineStatus.PENDING
    final_review_notes: Optional[str] = None
    final_review_notes_status: Optional[FinalReviewStatus] = None
    book_output_status: BookOutputStatus = BookOutputStatus.PENDING
    output_file_url: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Outline Schemas ───────────────────────────────────────────────────────────


class OutlineApproval(BaseModel):
    """Editor approves outline or adds post-outline notes."""
    status_outline: OutlineStatus = Field(
        ..., description="Set to 'approved' or 'notes_requested'"
    )
    notes_on_outline_after: Optional[str] = Field(
        None, description="Post-outline editor notes"
    )


# ── Chapter Schemas ───────────────────────────────────────────────────────────


class ChapterResponse(BaseModel):
    """Full chapter record returned from the API."""
    id: UUID
    book_id: UUID
    chapter_number: int
    title: Optional[str] = None
    content: Optional[str] = None
    summary: Optional[str] = None
    chapter_notes: Optional[str] = None
    chapter_notes_status: Optional[NotesStatus] = None
    status: ChapterStatus = ChapterStatus.PENDING
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ChapterNotesUpdate(BaseModel):
    """Editor adds notes or approves a chapter."""
    chapter_notes: Optional[str] = None
    chapter_notes_status: NotesStatus = Field(
        ..., description="yes / no / no_notes_needed"
    )


# ── Compilation Schemas ───────────────────────────────────────────────────────


class CompilationRequest(BaseModel):
    """Trigger final book compilation."""
    format: str = Field(
        default="docx", description="Export format: docx, pdf, or txt"
    )


class CompilationResponse(BaseModel):
    """Result of compilation."""
    book_id: UUID
    status: BookOutputStatus
    output_file_url: Optional[str] = None
    message: str


# ── Import Schemas ────────────────────────────────────────────────────────────


class ImportResult(BaseModel):
    """Result of a batch import from Excel."""
    books_created: int
    book_ids: list[UUID]


# ── Generic ───────────────────────────────────────────────────────────────────


class MessageResponse(BaseModel):
    """Simple message wrapper."""
    message: str
    book_id: Optional[UUID] = None
