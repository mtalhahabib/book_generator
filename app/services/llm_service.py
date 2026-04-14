"""LLM service — Google Gemini SDK, optimized for free-tier quotas.

Free-Tier Quota Reference (April 2026, per project):
┌──────────────────────────┬──────┬──────────────────────────────────────────┐
│ Model                    │  RPM │ Notes                                    │
├──────────────────────────┼──────┼──────────────────────────────────────────┤
│ gemini-2.5-flash         │   10 │ Best quality, implicit cache on 2.5+     │
│ gemini-2.5-flash-lite    │   30 │ Fast/cheap, implicit cache on 2.5+       │
│ gemini-2.0-flash         │   15 │ Deprecated but active                    │
│ gemini-2.0-flash-lite    │   30 │ Deprecated but active                    │
└──────────────────────────┴──────┴──────────────────────────────────────────┘

NOTE: gemini-1.5-* NOT available in v1beta — excluded from all fallback chains.

v4 Optimization strategy:
  1. WHOLE CHAPTER IN ONE CALL — 1 API call/chapter instead of 7.
  2. OUTLINE PREFIX — implicit caching on Gemini 2.5 for repeated tokens.
  3. DROP SUMMARIZE — free snippet extraction replaces API call.
  4. BATCH API support — all chapters submitted simultaneously.
  5. PER-MODEL COOLDOWN TRACKING — never fires at a known rate-limited model.
     When a model returns 429, marks it unavailable until its retry-after
     expires. Next model falls through ONLY after checking its own cooldown.
  6. 503 vs 429 DIFFERENTIATION — 503 = transient; retry immediately (2s
     wait). 429 = quota; honor full retry-after and skip to next model.
  7. GLOBAL CROSS-MODEL PAUSE — when 2+ consecutive models fail instantly
     (< 2s), all are quota-saturated; pause 60s before continuing.
  8. Token-bucket rate limiter (pre-throttle) per model.
  9. LRU response cache (identical prompts cost zero quota).
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Model registry
# ─────────────────────────────────────────────────────────────────────────────

_SAFE_RPM: dict[str, float] = {
    "gemini-2.5-flash":      8.0,
    "gemini-2.5-flash-lite": 24.0,
    "gemini-2.0-flash":      12.0,
    "gemini-2.0-flash-lite": 24.0,
}

_FALLBACK_CHAIN: dict[str, list[str]] = {
    "gemini-2.5-flash":      ["gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.0-flash-lite"],
    "gemini-2.5-flash-lite": ["gemini-2.0-flash-lite"],
    "gemini-2.0-flash":      ["gemini-2.0-flash-lite"],
    "gemini-2.0-flash-lite": [],
}

# Task → model routing
_MODEL_FOR_TASK: dict[str, str] = {
    "outline":   "gemini-2.5-flash",
    "chapter":   "gemini-2.5-flash",
    "research":  "gemini-2.5-flash",
}

# Limits
_MAX_503_RETRIES   = 2     # quick transient retries per model
_MAX_WAIT          = 65.0  # hard cap on any single sleep
_BASE_WAIT         = 3.0   # fallback when no retry-after hint given
_JITTER            = 0.10  # ±10% jitter on actual sleeps
_GLOBAL_PAUSE      = 60.0  # pause when 2+ models fail instantly in a row
_INSTANT_FAIL_SECS = 2.0   # response time < this = "failed instantly"
_MAX_RETRY_CYCLES  = 3     # max times to restart the full fallback chain
_CACHE_CAPACITY    = 128


# ─────────────────────────────────────────────────────────────────────────────
# Singleton client
# ─────────────────────────────────────────────────────────────────────────────

_gemini_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        key = settings.gemini_api_key
        if not key or key in ("placeholder-key", "your_gemini_api_key_here"):
            raise ValueError("GEMINI_API_KEY not configured. Set it in .env.")
        _gemini_client = genai.Client(api_key=key)
        logger.info("Gemini client initialized (key=...%s).", key[-6:])
    return _gemini_client


# ─────────────────────────────────────────────────────────────────────────────
# Per-model state: token-bucket + cooldown tracking
# ─────────────────────────────────────────────────────────────────────────────

class _TokenBucket:
    """Thread-safe token-bucket rate limiter."""
    def __init__(self, rpm: float) -> None:
        self._interval  = 60.0 / rpm
        self._lock      = threading.Lock()
        self._next_slot = time.monotonic()

    def acquire(self) -> None:
        with self._lock:
            wait = self._next_slot - time.monotonic()
            if wait > 0:
                logger.debug("Rate-limiter sleeping %.2fs.", wait)
                time.sleep(wait)
            self._next_slot = time.monotonic() + self._interval


class _ModelState:
    """Combines a token-bucket with explicit cooldown tracking.

    When a model returns 429, we record exactly when it will be usable
    again. Before the next attempt we sleep until that time instead of
    blindly firing and getting another 429 (which wastes an API quota slot).
    """

    def __init__(self, rpm: float) -> None:
        self._bucket       = _TokenBucket(rpm)
        self._lock         = threading.Lock()
        self._available_at = 0.0   # monotonic time when model is usable

    def set_cooldown(self, seconds: float) -> None:
        """Mark this model as rate-limited for `seconds` from now."""
        deadline = time.monotonic() + max(seconds, 1.0)
        with self._lock:
            # Only ever extend the cooldown, never shorten it
            if deadline > self._available_at:
                self._available_at = deadline

    def wait_and_acquire(self) -> float:
        """Block until cooldown expires, then acquire a rate-limit slot.

        Returns total seconds spent waiting.
        """
        with self._lock:
            remaining = self._available_at - time.monotonic()

        waited = 0.0
        if remaining > 0:
            jitter = remaining * _JITTER * (random.random() * 2 - 1)
            sleep  = min(max(remaining + jitter, 0), _MAX_WAIT)
            logger.info("Model cooldown active — sleeping %.1fs before retry.", sleep)
            time.sleep(sleep)
            waited += sleep

        self._bucket.acquire()
        return waited

    def is_in_cooldown(self) -> bool:
        return time.monotonic() < self._available_at

    def cooldown_remaining(self) -> float:
        return max(0.0, self._available_at - time.monotonic())


_model_states: dict[str, _ModelState] = {}
_states_lock = threading.Lock()


def _get_state(model: str) -> _ModelState:
    with _states_lock:
        if model not in _model_states:
            rpm = _SAFE_RPM.get(model, 8.0)
            _model_states[model] = _ModelState(rpm)
            logger.info("Model state initialized: '%s' @ %.1f RPM.", model, rpm)
        return _model_states[model]


# ─────────────────────────────────────────────────────────────────────────────
# LRU cache
# ─────────────────────────────────────────────────────────────────────────────

class _LRUCache:
    def __init__(self, capacity: int) -> None:
        self._cap   = capacity
        self._store: OrderedDict[str, str] = OrderedDict()
        self._lock  = threading.Lock()
        self.hits   = 0
        self.misses = 0

    def _key(self, model: str, prompt: str) -> str:
        return hashlib.sha256(f"{model}||{prompt}".encode()).hexdigest()

    def get(self, model: str, prompt: str) -> str | None:
        k = self._key(model, prompt)
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
                self.hits += 1
                return self._store[k]
            self.misses += 1
            return None

    def put(self, model: str, prompt: str, text: str) -> None:
        k = self._key(model, prompt)
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
            else:
                if len(self._store) >= self._cap:
                    self._store.popitem(last=False)
            self._store[k] = text

    def stats(self) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "hits":     self.hits,
            "misses":   self.misses,
            "hit_rate": f"{self.hits/total:.1%}" if total else "n/a",
            "size":     len(self._store),
            "capacity": self._cap,
        }


_cache = _LRUCache(_CACHE_CAPACITY)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_retry_after(message: str) -> float | None:
    m = re.search(r"retry\s+(?:in|after)\s+([\d.]+)\s*s", message, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"seconds[:\s]+([\d.]+)", message, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def _jitter_sleep(seconds: float) -> float:
    """Sleep `seconds` with ±JITTER%, capped at _MAX_WAIT. Returns actual sleep."""
    actual = min(max(seconds * (1 + _JITTER * (random.random() * 2 - 1)), 1.0), _MAX_WAIT)
    time.sleep(actual)
    return actual


def _trim(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[trimmed]"


def extract_chapter_snippet(chapter_content: str, max_chars: int = 800) -> str:
    """Free context snippet — replaces summarize_chapter API call."""
    lines   = [l for l in chapter_content.strip().split("\n") if l.strip() and not l.startswith("#")]
    snippet = " ".join(lines)
    return snippet[:max_chars]


# ─────────────────────────────────────────────────────────────────────────────
# Core engine
# ─────────────────────────────────────────────────────────────────────────────

def _call(
    model: str,
    prompt: str,
    config: types.GenerateContentConfig,
    use_cache: bool = True,
) -> str:
    """Gemini API call with per-model cooldown awareness, retry & fallback.

    503 vs 429 distinction:
      503 = Transient service error. Retry the SAME model immediately (2s wait).
            Does NOT set a cooldown. Max _MAX_503_RETRIES per model.
      429 = Rate limit hit. Sets model cooldown = retry-after hint.
            Falls through to next model immediately (no retry on same model).

    Cross-model protection:
      If 2+ consecutive models fail within _INSTANT_FAIL_SECS of being called
      (meaning the APIs are truly saturated), apply a _GLOBAL_PAUSE before
      continuing — prevents rapid-fire calls that waste all quota slots.

    Retry cycles:
      After ALL models in the chain are exhausted, find the shortest remaining
      cooldown across the whole chain, wait for it to expire, then restart
      the full fallback chain from the primary model. Repeats up to
      _MAX_RETRY_CYCLES times before giving up with RuntimeError.
    """
    client     = _get_client()
    all_models = [model] + _FALLBACK_CHAIN.get(model, [])

    for cycle in range(_MAX_RETRY_CYCLES + 1):
        instant_failures = 0  # reset per cycle
        exhausted_this_cycle = True  # assume failure unless we return

        for idx, model_name in enumerate(all_models):
            is_primary = idx == 0
            state      = _get_state(model_name)

            if not is_primary:
                logger.warning("→ Falling back to '%s'.", model_name)

            # ── Wait out this model's known cooldown before firing ────────────
            remaining = state.cooldown_remaining()
            if remaining > 0:
                logger.info(
                    "[%s] Known cooldown: %.0fs remaining — waiting before attempt.",
                    model_name, remaining,
                )
                state.wait_and_acquire()
            else:
                state._bucket.acquire()  # just the normal rate-limit slot

            # ── Cache check ───────────────────────────────────────────────────
            if use_cache:
                cached = _cache.get(model_name, prompt)
                if cached is not None:
                    logger.info("Cache hit: model='%s'.", model_name)
                    return cached

            # ── 503 retry loop (transient errors only) ────────────────────────
            for attempt_503 in range(_MAX_503_RETRIES + 1):
                call_start = time.monotonic()

                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=config,
                    )
                    text = resp.text
                    if use_cache:
                        _cache.put(model_name, prompt, text)
                    if not is_primary:
                        logger.info("Fallback '%s' succeeded.", model_name)
                    return text  # SUCCESS — exits all loops

                except APIError as exc:
                    code    = exc.code
                    msg     = str(exc.message)
                    elapsed = time.monotonic() - call_start

                    # ── 404: model doesn't exist ─────────────────────────────
                    if code == 404:
                        logger.error("[%s] 404 — model not found. Skipping.", model_name)
                        break  # try next model

                    # ── 503: transient — retry quickly on same model ──────────
                    elif code == 503:
                        if attempt_503 < _MAX_503_RETRIES:
                            logger.warning(
                                "[%s] 503 transient (attempt %d/%d) — retry in 2s.",
                                model_name, attempt_503 + 1, _MAX_503_RETRIES,
                            )
                            time.sleep(2.0)
                            state._bucket.acquire()
                            continue
                        else:
                            logger.warning("[%s] 503 retries exhausted — next model.", model_name)
                            break

                    # ── 429: rate limited — set cooldown, move to next model ──
                    elif code == 429:
                        hint     = _parse_retry_after(msg)
                        cooldown = hint if hint else _BASE_WAIT
                        state.set_cooldown(cooldown)

                        logger.warning(
                            "[%s] 429 rate-limited (elapsed %.1fs). "
                            "Cooldown set: %.0fs. Moving to next model.",
                            model_name, elapsed, cooldown,
                        )

                        if elapsed < _INSTANT_FAIL_SECS:
                            instant_failures += 1
                            logger.warning(
                                "Instant failure #%d on '%s'.",
                                instant_failures, model_name,
                            )
                            if instant_failures >= 2:
                                logger.warning(
                                    "%d instant failures — %.0fs global pause.",
                                    instant_failures, _GLOBAL_PAUSE,
                                )
                                time.sleep(_GLOBAL_PAUSE)
                                instant_failures = 0
                        else:
                            instant_failures = 0

                        break  # move to next fallback model

                    else:
                        logger.error("[%s] APIError %s: %s", model_name, code, msg[:300])
                        raise

                except Exception as exc:
                    logger.error("[%s] Unexpected: %s", model_name, exc)
                    raise

        # ── All models in chain exhausted this cycle ──────────────────────────
        if cycle < _MAX_RETRY_CYCLES:
            # Find the shortest cooldown remaining across ALL models in chain
            cooldowns = [(m, _get_state(m).cooldown_remaining()) for m in all_models]
            active    = [(m, c) for m, c in cooldowns if c > 0]

            if active:
                min_model, min_wait = min(active, key=lambda x: x[1])
                wait_with_buffer    = min_wait + 2.0  # small safety buffer
                logger.warning(
                    "All models exhausted (cycle %d/%d). "
                    "Shortest cooldown: '%s' recovers in %.0fs. "
                    "Waiting then retrying full chain.",
                    cycle + 1, _MAX_RETRY_CYCLES + 1,
                    min_model, wait_with_buffer,
                )
                time.sleep(wait_with_buffer)
            else:
                # All cooldowns cleared but still failed — brief pause then retry
                logger.warning(
                    "All models exhausted (cycle %d/%d) with no active cooldowns. "
                    "Brief 5s pause then retrying.",
                    cycle + 1, _MAX_RETRY_CYCLES + 1,
                )
                time.sleep(5.0)

    raise RuntimeError(
        f"All Gemini models exhausted after {_MAX_RETRY_CYCLES + 1} retry cycles: {all_models}.\n"
        "Root causes:\n"
        "  1. Free-tier DAILY quota (RPD) exhausted — resets at midnight Pacific time.\n"
        "  2. All models simultaneously rate-limited — try again in 1-2 minutes.\n"
        "  3. Wrong/expired API key — verify GEMINI_API_KEY in .env.\n"
        "Check usage: https://aistudio.google.com/rate-limit"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────────────────────

def get_quota_stats() -> dict[str, Any]:
    """Live stats for monitoring free-tier consumption."""
    return {
        "cache": _cache.stats(),
        "models": {
            name: {
                "safe_rpm":          _SAFE_RPM.get(name, "?"),
                "interval_s":        f"{s._bucket._interval:.2f}",
                "cooldown_remaining": f"{s.cooldown_remaining():.0f}s",
                "in_cooldown":       s.is_in_cooldown(),
            }
            for name, s in _model_states.items()
        },
        "model_routing": _MODEL_FOR_TASK,
    }


# ─────────────────────────────────────────────────────────────────────────────
# ── Outline Generation ────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

_OUTLINE_SYSTEM = (
    "You are a professional book outline architect. "
    "Given a title and optional editorial notes, produce a well-structured "
    "book outline with numbered chapters. Format EACH chapter as:\n\n"
    "## Chapter N: Title\n"
    "Brief 2-3 sentence description of the chapter content.\n\n"
    "Be comprehensive and consistent. Output clean Markdown only."
)


def generate_outline(title: str, notes: str | None = None) -> str:
    """Generate a Markdown book outline. Cached — retrying costs zero quota."""
    prompt = f"Book Title: {title}\n"
    if notes:
        prompt += f"\nEditorial Notes:\n{_trim(notes, 2000)}\n"
    prompt += "\nGenerate the full book outline now."

    logger.info("Generating outline for: '%s'", title)
    return _call(
        model=_MODEL_FOR_TASK["outline"],
        prompt=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_OUTLINE_SYSTEM,
            temperature=0.7,
            max_output_tokens=4096,
        ),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ── Chapter Generation (ONE CALL) ─────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

_CHAPTER_SYSTEM = (
    "You are a professional non-fiction book author. "
    "Write a complete, well-structured book chapter divided into sections. "
    "\n\nCRITICAL FORMATTING RULE — YOU MUST FOLLOW THIS EXACTLY:"
    "\nDivide the chapter into exactly 5 sections."
    "\nEach section MUST start with a markdown level-2 heading on its own line, like this:"
    "\n## Section Title Here"
    "\nDo NOT use any other heading format. Do NOT skip the ## headings."
    "\n\nEach section should be 400-600 words."
    "\nStyle: clear, engaging, authoritative."
    "\nUse bullet points and examples where appropriate."
    "\nDo NOT add a general chapter intro before the first ## heading."
)


def generate_chapter(
    book_title: str,
    chapter_number: int,
    chapter_title: str,
    outline: str,
    previous_chapter_snippet: str | None = None,
    chapter_notes: str | None = None,
) -> str:
    """Generate a FULL chapter in a single API call.

    Outline placed first in prompt to exploit Gemini 2.5 implicit prefix caching.
    The ~2000 outline tokens are cached server-side after chapter 1 → 0 cost.
    """
    # Outline FIRST — triggers Gemini 2.5 implicit cache on repeated prefix
    prompt = f"=== BOOK OUTLINE ===\n{_trim(outline, 8000)}\n=== END OUTLINE ===\n\n"

    if previous_chapter_snippet:
        prompt += (
            f"=== PREVIOUS CHAPTER CONTEXT ===\n"
            f"{_trim(previous_chapter_snippet, 800)}\n"
            f"=== END CONTEXT ===\n\n"
        )

    if chapter_notes:
        prompt += f"Editor notes: {_trim(chapter_notes, 400)}\n\n"

    prompt += (
        f"Book: {book_title}\n"
        f"Write Chapter {chapter_number}: {chapter_title}\n"
        f"Write the complete chapter now with 4-6 sections using ## headers."
    )

    logger.info("Generating Chapter %d '%s' (1 API call, outline-prefix cached).", chapter_number, chapter_title)

    return _call(
        model=_MODEL_FOR_TASK["chapter"],
        prompt=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_CHAPTER_SYSTEM,
            temperature=0.65,
            max_output_tokens=8000,
        ),
        use_cache=False,  # chapter content must always be uniquely generated
    )


def parse_sections_from_chapter(chapter_content: str) -> list[dict[str, str]]:
    """Parse ## headers from a generated chapter into section dicts (no API call).

    Primary path: split on ## headings.
    Fallback path: if the model ignored ## headings (e.g. flash-lite), split
    the text into 5 roughly equal sections by paragraph boundaries.
    This prevents the entire chapter from landing in a single 'Content' blob.
    """
    sections: list[dict[str, str]] = []
    current_title = ""
    current_lines: list[str] = []

    for line in chapter_content.split("\n"):
        if line.startswith("## "):
            if current_title:
                sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
            current_title = line[3:].strip()
            current_lines = []
        elif line.startswith("# "):
            continue  # skip chapter heading
        else:
            current_lines.append(line)

    if current_title:
        sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})

    # ── Fallback: no ## headers found → split by paragraphs into 5 sections ──
    if not sections and chapter_content.strip():
        logger.warning(
            "No ## sections found — model ignored format. "
            "Auto-splitting by paragraph into 5 sections."
        )
        # Split into paragraph blocks (double newline)
        paragraphs = [
            p.strip() for p in chapter_content.strip().split("\n\n")
            if p.strip() and not p.strip().startswith("#")
        ]

        if paragraphs:
            target = 5
            chunk  = max(1, len(paragraphs) // target)
            chunks = []
            for i in range(0, len(paragraphs), chunk):
                chunks.append("\n\n".join(paragraphs[i : i + chunk]))
                if len(chunks) == target:
                    # Append remaining paragraphs to last chunk
                    remaining = paragraphs[i + chunk :]
                    if remaining:
                        chunks[-1] += "\n\n" + "\n\n".join(remaining)
                    break

            section_labels = [
                "Overview", "Core Concepts", "Key Developments",
                "Implications", "Looking Ahead",
            ]
            for j, chunk_text in enumerate(chunks):
                label = section_labels[j] if j < len(section_labels) else f"Part {j+1}"
                sections.append({"title": label, "content": chunk_text})
        else:
            sections.append({"title": "Content", "content": chapter_content.strip()})

    return sections


# ─────────────────────────────────────────────────────────────────────────────
# ── Batch API helpers ──────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def build_chapter_batch_requests(
    book_title: str,
    chapters: list[dict],
    outline: str,
) -> list[dict]:
    """Build Gemini Batch API request list for all chapters simultaneously."""
    requests = []
    for ch in chapters:
        ch_num   = ch["chapter_number"]
        ch_title = ch.get("title", f"Chapter {ch_num}")
        notes    = ch.get("chapter_notes")

        prompt = (
            f"=== BOOK OUTLINE ===\n{_trim(outline, 8000)}\n=== END OUTLINE ===\n\n"
            f"Book: {book_title}\n"
            f"Write Chapter {ch_num}: {ch_title}\n"
        )
        if notes:
            prompt += f"Editor notes: {_trim(notes, 400)}\n"
        prompt += "Write the complete chapter with 4-6 sections using ## headers."

        requests.append({
            "key": f"chapter-{ch_num}",
            "request": {
                "contents": [{"parts": [{"text": prompt}], "role": "user"}],
                "system_instruction": {"parts": [{"text": _CHAPTER_SYSTEM}]},
                "generation_config": {"temperature": 0.65, "max_output_tokens": 8000},
            },
        })
    return requests


def submit_batch_job(requests: list[dict], display_name: str) -> str:
    """Submit Batch job and return job name. No RPM limits apply."""
    client = _get_client()
    job    = client.batches.create(
        model=_MODEL_FOR_TASK["chapter"],
        src=requests,
        config={"display_name": display_name},
    )
    logger.info("Batch job: %s (%d requests)", job.name, len(requests))
    return job.name


def poll_batch_job(job_name: str) -> dict:
    """Poll batch job state. Returns {state, done, responses}."""
    client = _get_client()
    job    = client.batches.get(name=job_name)
    state  = job.state.name
    done   = state in ("JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")
    result: dict[str, Any] = {"state": state, "done": done, "responses": {}}

    if done and state == "JOB_STATE_SUCCEEDED":
        for ir in (job.dest.inlined_responses or []):
            key = getattr(ir, "key", None)
            if key and ir.response:
                result["responses"][key] = ir.response.text

    return result


# ─────────────────────────────────────────────────────────────────────────────
# ── Research ──────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

def research_topic(query: str) -> str:
    """Research with Google Search grounding, falls back to plain."""
    system_prompt = (
        "You are a research assistant. Use Google Search to find accurate, "
        "up-to-date information. Cite sources inline. "
        "Provide a well-organized summary with key facts."
    )
    trimmed = _trim(query, 1000)
    try:
        return _call(
            model=_MODEL_FOR_TASK["research"],
            prompt=trimmed,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=4096,
                tools=[{"google_search": {}}],
            ),
        )
    except Exception as exc:
        logger.warning("Grounded research failed (%s) — using plain.", exc)
        return _call(
            model=_MODEL_FOR_TASK["research"],
            prompt=trimmed,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
                max_output_tokens=4096,
            ),
        )
