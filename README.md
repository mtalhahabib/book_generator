# 📚 Automated Book Generation System

A modular, scalable book-generation system built with **FastAPI**, **Supabase**, and **Google Gemini SDK**.  
Accepts a title, generates an outline, writes chapters with feedback-based gating, and compiles a final draft.

## Tech Stack

| Component | Tool |
|---|---|
| Backend / Automation | Python 3.12 + FastAPI |
| Database | Supabase (PostgreSQL + Storage) |
| AI Model | Google Gemini (gemini-2.5-flash + Google Search Grounding) |
| Input Source | Excel (.xlsx) / CSV file upload |
| Notifications | Email (SMTP) + MS Teams Webhooks |
| Output Files | .docx, .pdf, .txt |
| Editor UI | Built-in HTML/JS dashboard |

## Architecture

```
FastAPI Server
├── app/
│   ├── config.py           # Pydantic Settings (.env loader)
│   ├── models/
│   │   ├── enums.py         # Status enumerations
│   │   └── schemas.py       # Pydantic request/response models
│   ├── services/
│   │   ├── db_service.py    # Supabase CRUD operations
│   │   ├── llm_service.py   # Google Gemini SDK integration
│   │   ├── notification_service.py  # Email + Teams
│   │   ├── export_service.py        # .docx/.pdf/.txt generation
│   │   └── input_service.py         # Excel/CSV reader
│   ├── pipelines/
│   │   ├── outline_pipeline.py    # Stage 1: Outline generation
│   │   ├── chapter_pipeline.py    # Stage 2: Chapter generation
│   │   └── compilation_pipeline.py # Stage 3: Final compilation
│   ├── routes/
│   │   ├── books.py         # Book CRUD + import
│   │   ├── outlines.py      # Outline generate/approve
│   │   ├── chapters.py      # Chapter generate/review/approve
│   │   └── compilation.py   # Compile + download
│   └── static/
│       └── index.html       # Editor review dashboard
├── main.py                  # FastAPI app entry point
├── supabase_migration.sql   # Database schema migration
├── requirements.txt
└── .env.example
```

## Setup

### 1. Database (Supabase)

1. Create a project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and run `supabase_migration.sql`
3. Create a **Storage bucket** named `book-exports` (public)

### 2. Environment Setup

Copy the example environment file and fill in your keys:

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```
**Mac/Linux:**
```bash
cp .env.example .env
```
*Edit `.env` and add your `SUPABASE_URL`, `SUPABASE_KEY`, and `GEMINI_API_KEY`.*

### 3. Install & Run

It is highly recommended to run this inside a virtual environment.

**Windows:**
```powershell
# Create virtual environment
python -m venv venv

# Activate it
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the backend server
uvicorn main:app --reload
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### 4. Open Dashboard

Navigate to **http://localhost:8000/static/index.html**

## Workflow

### Stage 1: Outline
1. Create a book (title + pre-outline notes)
2. System generates an outline via Gemini AI
3. Editor reviews → approves or adds notes → regenerate

### Stage 2: Chapters
4. Generate chapters one-by-one or all at once
5. Each chapter uses previous chapter summaries as context
6. Editor reviews → approves or adds notes → regenerate

### Stage 3: Compile
7. Set final review status
8. Compile to .docx / .pdf / .txt
9. Download the final draft

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/books` | Create book |
| `POST` | `/api/books/import` | Batch import from Excel |
| `GET` | `/api/books` | List all books |
| `GET` | `/api/books/{id}` | Get book detail |
| `PATCH` | `/api/books/{id}` | Update book fields |
| `POST` | `/api/books/{id}/outline/generate` | Generate outline |
| `POST` | `/api/books/{id}/outline/approve` | Approve/request outline changes |
| `POST` | `/api/books/{id}/chapters/generate` | Generate next chapter |
| `POST` | `/api/books/{id}/chapters/generate/{n}` | Generate specific chapter |
| `POST` | `/api/books/{id}/chapters/generate-all` | Generate all remaining |
| `GET` | `/api/books/{id}/chapters` | List chapters |
| `PATCH` | `/api/books/{id}/chapters/{n}` | Add notes / approve chapter |
| `POST` | `/api/books/{id}/compile` | Compile final draft |
| `GET` | `/api/books/{id}/download` | Download compiled file |
| `GET` | `/health` | Health check |

## Notifications

The system sends notifications via **Email** and **MS Teams** on:
- ✅ Outline ready for review
- ⏳ Waiting for chapter notes
- ✅ Final draft compiled
- ⚠️ Pipeline paused (missing input)
