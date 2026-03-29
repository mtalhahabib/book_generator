"""Book-level API routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.schemas import (
    BookCreate,
    BookResponse,
    BookUpdate,
    ImportResult,
    MessageResponse,
)
from app.services import db_service, input_service

router = APIRouter(prefix="/api/books", tags=["Books"])


@router.post("", response_model=BookResponse, status_code=201)
def create_book(body: BookCreate):
    """Create a new book with title and optional pre-outline notes."""
    row = db_service.create_book(
        title=body.title,
        notes_on_outline_before=body.notes_on_outline_before,
    )
    return row


@router.post("/import", response_model=ImportResult)
async def import_books(file: UploadFile = File(...)):
    """Batch import books from an uploaded Excel (.xlsx) or CSV file."""
    if not file.filename:
        raise HTTPException(400, "No file uploaded.")

    content = await file.read()
    try:
        inputs = input_service.read_bytes_excel(content, file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    book_ids: list[UUID] = []
    for inp in inputs:
        row = db_service.create_book(
            title=inp.title,
            notes_on_outline_before=inp.notes_on_outline_before,
        )
        book_ids.append(row["id"])

    return ImportResult(books_created=len(book_ids), book_ids=book_ids)


@router.get("", response_model=list[BookResponse])
def list_books():
    """List all books ordered by creation date (newest first)."""
    return db_service.list_books()


@router.get("/{book_id}", response_model=BookResponse)
def get_book(book_id: UUID):
    """Retrieve a single book by ID."""
    book = db_service.get_book(str(book_id))
    if not book:
        raise HTTPException(404, "Book not found.")
    return book


@router.patch("/{book_id}", response_model=BookResponse)
def update_book(book_id: UUID, body: BookUpdate):
    """Update book-level fields (notes, status, etc.)."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "No fields to update.")

    # Convert enums to their string values
    for key, val in updates.items():
        if hasattr(val, "value"):
            updates[key] = val.value

    try:
        return db_service.update_book(str(book_id), updates)
    except Exception as exc:
        raise HTTPException(400, str(exc))
