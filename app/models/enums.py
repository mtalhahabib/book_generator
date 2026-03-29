"""Status enums used across the application."""

from enum import Enum


class OutlineStatus(str, Enum):
    """Tracks the lifecycle of a book's outline."""
    PENDING = "pending"
    OUTLINE_GENERATED = "outline_generated"
    NOTES_REQUESTED = "notes_requested"
    APPROVED = "approved"


class ChapterStatus(str, Enum):
    """Tracks the lifecycle of an individual chapter."""
    PENDING = "pending"
    GENERATING = "generating"
    GENERATED = "generated"
    NOTES_REQUESTED = "notes_requested"
    APPROVED = "approved"


class NotesStatus(str, Enum):
    """Editor's response on whether they want to add notes."""
    YES = "yes"
    NO = "no"
    NO_NOTES_NEEDED = "no_notes_needed"


class FinalReviewStatus(str, Enum):
    """Status of the final review stage."""
    YES = "yes"
    NO = "no"
    NO_NOTES_NEEDED = "no_notes_needed"


class BookOutputStatus(str, Enum):
    """Overall book output status."""
    PENDING = "pending"
    COMPILING = "compiling"
    READY = "ready"
    PAUSED = "paused"
