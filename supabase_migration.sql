-- ============================================================
-- Supabase Migration: Book Generator Schema
-- Run this in the Supabase SQL Editor
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── Books ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS books (
    id                       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title                    TEXT NOT NULL,
    notes_on_outline_before  TEXT,
    outline                  TEXT,
    notes_on_outline_after   TEXT,
    status_outline           TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status_outline IN ('pending', 'outline_generated', 'notes_requested', 'approved')),
    final_review_notes       TEXT,
    final_review_notes_status TEXT
                             CHECK (final_review_notes_status IN ('yes', 'no', 'no_notes_needed')),
    book_output_status       TEXT NOT NULL DEFAULT 'pending'
                             CHECK (book_output_status IN ('pending', 'compiling', 'ready', 'paused')),
    output_file_url          TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Chapters ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chapters (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    book_id             UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_number      INT NOT NULL,
    title               TEXT,
    content             TEXT,
    summary             TEXT,
    chapter_notes       TEXT,
    chapter_notes_status TEXT
                        CHECK (chapter_notes_status IN ('yes', 'no', 'no_notes_needed')),
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'generating', 'generated', 'notes_requested', 'approved')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(book_id, chapter_number)
);

-- ── Sections ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sections (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    chapter_id          UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    title               TEXT NOT NULL,
    content             TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'generating', 'done')),
    order_index         INT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Notification Log ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS notification_log (
    id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    book_id   UUID NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    event     TEXT NOT NULL,
    channel   TEXT NOT NULL,
    payload   JSONB,
    sent_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ──────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_chapters_book_id ON chapters(book_id);
CREATE INDEX IF NOT EXISTS idx_sections_chapter_id ON sections(chapter_id);
CREATE INDEX IF NOT EXISTS idx_notification_log_book_id ON notification_log(book_id);

-- ── Auto-update updated_at ───────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_books_updated_at
    BEFORE UPDATE ON books
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_chapters_updated_at
    BEFORE UPDATE ON chapters
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_sections_updated_at
    BEFORE UPDATE ON sections
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── Enable Row Level Security (optional, recommended) ────────

ALTER TABLE books ENABLE ROW LEVEL SECURITY;
ALTER TABLE chapters ENABLE ROW LEVEL SECURITY;
ALTER TABLE sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_log ENABLE ROW LEVEL SECURITY;

-- Allow all operations for authenticated users (adjust as needed)
CREATE POLICY "Allow all for authenticated" ON books
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for authenticated" ON chapters
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for authenticated" ON sections
    FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for authenticated" ON notification_log
    FOR ALL USING (true) WITH CHECK (true);
