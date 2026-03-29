"""LLM service — Google Gemini SDK integration for outline & chapter generation."""

from __future__ import annotations

import logging
from google import genai
from google.genai import types

from app.config import settings

logger = logging.getLogger(__name__)

_gemini_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """Lazily create the Gemini client."""
    global _gemini_client
    if _gemini_client is None:
        if not settings.gemini_api_key or settings.gemini_api_key in ("placeholder-key", "your_gemini_api_key_here"):
            raise ValueError("GEMINI_API_KEY is not configured or is a placeholder.")
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


# ── Outline Generation ────────────────────────────────────────────────────────


def generate_outline(title: str, notes: str | None = None) -> str:
    """Generate a structured book outline from a title and optional notes.

    Returns the outline as a formatted string (Markdown).
    """
    system_prompt = (
        "You are a professional book outline architect. "
        "Given a title and optional editorial notes, produce a well-structured "
        "book outline with numbered chapters. Each chapter entry should include "
        "a chapter title and a brief 2-3 sentence description of its content. "
        "Output in Markdown format."
    )

    user_prompt = f"Book Title: {title}\n"
    if notes:
        user_prompt += f"\nEditorial Notes / Guidance:\n{notes}\n"
    user_prompt += (
        "\nPlease generate a comprehensive book outline with chapter titles "
        "and brief descriptions."
    )

    response = _get_client().models.generate_content(
        model="gemini-2.5-flash",
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
            max_output_tokens=4096,
        )
    )
    return response.text


# ── Chapter Generation ────────────────────────────────────────────────────────


def generate_chapter(
    book_title: str,
    outline: str,
    chapter_title: str,
    chapter_number: int,
    total_chapters: int,
    previous_summaries: list[str] | None = None,
    chapter_notes: str | None = None,
) -> str:
    """Write a full chapter using the outline + previous chapter context."""
    system_prompt = (
        "You are a professional book author. Write a detailed, well-structured "
        "chapter for a book. The chapter should be engaging, informative, and "
        "maintain consistency with the overall book narrative. "
        "Use proper headings, paragraphs, and formatting in Markdown. "
        "Aim for substantial, publication-quality content."
    )

    user_prompt = f"# Book: {book_title}\n\n"
    user_prompt += f"## Full Book Outline\n{outline}\n\n"

    if previous_summaries:
        user_prompt += "## Context — Summaries of Previous Chapters\n"
        for i, summary in enumerate(previous_summaries, 1):
            user_prompt += f"### Chapter {i} Summary\n{summary}\n\n"

    user_prompt += (
        f"## Your Task\n"
        f"Write **Chapter {chapter_number}: {chapter_title}** "
        f"(chapter {chapter_number} of {total_chapters}).\n"
    )

    if chapter_notes:
        user_prompt += (
            f"\n### Editor Notes for This Chapter\n{chapter_notes}\n"
        )

    user_prompt += (
        "\nWrite the complete chapter with proper structure, headings, and "
        "engaging content. Make sure it flows naturally from the previous "
        "chapters and sets up the next ones."
    )

    response = _get_client().models.generate_content(
        model="gemini-2.5-flash",
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.7,
            max_output_tokens=8192,
        )
    )
    return response.text


# ── Chapter Summarization ─────────────────────────────────────────────────────


def summarize_chapter(chapter_content: str) -> str:
    """Produce a concise summary of a chapter for context chaining."""
    system_prompt = (
        "You are a concise summarizer. Produce a 3-5 sentence summary of the "
        "following book chapter. Capture the key points, arguments, and any "
        "narrative progression. This summary will be used as context for "
        "generating subsequent chapters."
    )

    response = _get_client().models.generate_content(
        model="gemini-2.5-flash",
        contents=chapter_content,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.3,
            max_output_tokens=1024,
        )
    )
    return response.text


# ── Research (Option C — Web Search via Gemini) ───────────────────────────────


def research_topic(query: str) -> str:
    """Use Gemini's Google Search grounding to research a topic."""
    system_prompt = (
        "You are a research assistant. Use the integrated Google Search to find accurate, "
        "up-to-date information on the given topic. Cite your sources. "
        "Provide a well-organized summary of findings."
    )

    try:
        response = _get_client().models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=4096,
                tools=[{"google_search": {}}],  # Enable Google Search grounding
            )
        )
    except Exception as exc:
        logger.error(f"Search failed, falling back to basic completion. Error: {exc}")
        # Fallback without search
        response = _get_client().models.generate_content(
            model="gemini-2.5-flash",
            contents=query,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=4096,
            )
        )

    return response.text
