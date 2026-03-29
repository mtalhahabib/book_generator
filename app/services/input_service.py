"""Input service — reads book data from Excel/CSV files for batch import."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from pydantic import BaseModel


class BookInput(BaseModel):
    """A single book input parsed from a spreadsheet row."""
    title: str
    notes_on_outline_before: Optional[str] = None


def read_excel(file_path: str | Path) -> list[BookInput]:
    """Read an Excel (.xlsx) or CSV file and return a list of BookInput items.

    Expected columns:
        - title (mandatory)
        - notes_on_outline_before (optional)
    """
    path = Path(file_path)
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, engine="openpyxl")

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    if "title" not in df.columns:
        raise ValueError(
            f"Input file must contain a 'title' column. Found: {list(df.columns)}"
        )

    results: list[BookInput] = []
    for _, row in df.iterrows():
        title = str(row["title"]).strip()
        if not title or title.lower() == "nan":
            continue
        notes = None
        if "notes_on_outline_before" in df.columns:
            raw = row.get("notes_on_outline_before")
            if pd.notna(raw):
                notes = str(raw).strip() or None
        results.append(BookInput(title=title, notes_on_outline_before=notes))

    return results


def read_bytes_excel(file_bytes: bytes, filename: str) -> list[BookInput]:
    """Read book inputs from uploaded file bytes (Excel or CSV).

    Used by the API endpoint that accepts file uploads.
    """
    import io

    if filename.lower().endswith(".csv"):
        df = pd.read_csv(io.BytesIO(file_bytes))
    else:
        df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")

    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    if "title" not in df.columns:
        raise ValueError(
            f"Input file must contain a 'title' column. Found: {list(df.columns)}"
        )

    results: list[BookInput] = []
    for _, row in df.iterrows():
        title = str(row["title"]).strip()
        if not title or title.lower() == "nan":
            continue
        notes = None
        if "notes_on_outline_before" in df.columns:
            raw = row.get("notes_on_outline_before")
            if pd.notna(raw):
                notes = str(raw).strip() or None
        results.append(BookInput(title=title, notes_on_outline_before=notes))

    return results
