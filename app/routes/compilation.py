"""Compilation API routes — compile and download the final book."""

from __future__ import annotations

import io
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models.schemas import CompilationRequest, CompilationResponse
from app.models.enums import BookOutputStatus
from app.pipelines import compilation_pipeline
from app.services import db_service, export_service

router = APIRouter(prefix="/api/books/{book_id}", tags=["Compilation"])


@router.post("/compile", response_model=CompilationResponse)
def compile_book(book_id: UUID, body: CompilationRequest = CompilationRequest()):
    """Trigger final compilation of the book.

    Requires all chapters to be approved and final review gating to pass.
    """
    try:
        updated = compilation_pipeline.compile_book(str(book_id), body.format)
        return CompilationResponse(
            book_id=book_id,
            status=BookOutputStatus(updated["book_output_status"]),
            output_file_url=updated.get("output_file_url"),
            message=f"Book compiled successfully as .{body.format}",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/download")
def download_book(book_id: UUID, format: str = "docx"):
    """Download the compiled book file.

    Generates the file on-the-fly from the stored chapters.
    """
    book = db_service.get_book(str(book_id))
    if not book:
        raise HTTPException(404, "Book not found.")

    chapters = db_service.get_chapters_for_book(str(book_id))
    if not chapters:
        raise HTTPException(400, "No chapters found.")

    content_type_map = {
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "pdf": "application/pdf",
        "txt": "text/plain; charset=utf-8",
    }

    if format not in content_type_map:
        raise HTTPException(400, f"Unsupported format: {format}. Use docx, pdf, or txt.")

    safe_title = "".join(
        c if c.isalnum() or c in " _-" else "_" for c in book["title"]
    ).strip()

    if format == "docx":
        file_bytes = export_service.export_docx(book, chapters)
    elif format == "pdf":
        file_bytes = export_service.export_pdf(book, chapters)
    else:
        txt = export_service.export_txt(book, chapters)
        file_bytes = txt.encode("utf-8")

    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=content_type_map[format],
        headers={
            "Content-Disposition": f'attachment; filename="{safe_title}.{format}"'
        },
    )
