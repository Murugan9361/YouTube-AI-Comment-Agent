# Advanced YouTube Auto Commenting Agent

A full-stack YouTube commenting automation platform featuring two independent workflows:
1. **Mode 1: AI-Based Single Video Commenting** — Generate contextual comments for any video using Google Gemini, OpenAI, or Ollama, review/edit, and publish.
2. **Mode 2: PDF-Based YouTube Channel Bulk Commenting** — Upload a PDF with 100+ pre-written comments, fetch a YouTube channel's videos, automatically distribute unique comments 1-to-1 without AI modification, and publish in a controlled, rate-limited batch.

---

## Architecture Overview

- **Backend**: FastAPI with Uvicorn.
- **Frontend / UI**: Streamlit 4-tab interface (Single Video AI, PDF Channel Bulk, Comment History, Settings).
- **Authentication**: Real Google OAuth 2.0 with PKCE (`code_challenge` / `code_verifier`), state validation, and channel eligibility verification.
- **YouTube Integration**: Official YouTube Data API v3 (`commentThreads.insert`, `channels.list`, `playlistItems.list` with `https://www.googleapis.com/auth/youtube.force-ssl` scope).
- **PDF Extraction Engine**: Fast digital PDF parser (`pypdf`) with numbered list, bullet point, and paragraph detection, multiline grouping, duplicate filtering, and scanned PDF detection.
- **Comment Distribution Engine**: Strict 1-to-1 matching (sequential or random) with zero duplicate assignments.
- **Persistence & Duplicate Prevention**: Local SQLite database (`comment_agent.db`) recording all runs, comment history, and preventing comment or video reuse across restarts.
- **Safe Execution Controls**: Configurable rate-limit delays, Start, Pause, Resume, and Stop controls.

---

## Project Structure

```
youtube-ai-comment-agent/
│
├── app/
│   ├── __init__.py
│   ├── main.py
│   │
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── comment.py
│   │   └── channel.py
│   │
│   └── services/
│       ├── __init__.py
│       ├── oauth_service.py
│       ├── youtube_service.py
│       ├── youtube_channel_service.py
│       ├── ai_service.py
│       ├── pdf_extractor.py
│       ├── comment_assignment.py
│       ├── comment_publisher.py
│       └── database.py
│
├── streamlit_app.py
├── requirements.txt
├── comment_agent.db (SQLite)
├── .env
├── .env.example
├── .gitignore
└── README.md
```

---

## Features & Modes

### 🎯 Mode 1: AI-Based Single Video Commenting
1. Enter any YouTube video URL (`watch?v=`, `youtu.be/`, `shorts/`, `embed/`).
2. Optionally enter custom instructions or tone.
3. Generate contextual comments using the configured AI provider.
4. Review and manually edit the comment.
5. Click **Publish Comment** to post via YouTube Data API v3.

### 📚 Mode 2: PDF-Based Channel Bulk Commenting
1. **Upload PDF**: Upload a document containing 100+ comments (numbered, bulleted, or paragraphs).
2. **Channel Resolution**: Enter a channel handle (`@channelname`), URL, or channel ID. Fetches public uploads playlist videos.
3. **Video Selection**: Select all, individual, or set max count; automatically exclude previously commented videos.
4. **1-to-1 Distribution Engine**: Assigns exactly one unique PDF comment per video (Sequential or Random). Original PDF wording is preserved with zero AI alterations.
5. **Assignment Preview**: Review the mapped table before publishing.
6. **Safe Publishing**: Configurable delay (2s to 30s), live progress bar, pause/resume/stop controls, and quota limit resilience.

### 📊 Tab 3: Comment History & Analytics
- Complete persistent log in SQLite: Timestamp, Video Title, Assigned Comment, Source (`pdf` or `ai`), Status (`success` or `failed`), Published Comment ID, and Error details.
- Search and filter by Status, Source, Run ID, or keywords.
- One-click CSV export.

### ⚙️ Tab 4: Settings & Configuration
- Channel connection & OAuth status.
- AI provider configuration.
- Rate-limiting delay & duplicate prevention settings.

---

## Installation & Setup

1. **Activate virtual environment**:
   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

2. **Install dependencies**:
   ```bash
   python -m pip install -r requirements.txt
   ```

3. **Configure Environment Variables** in `.env`:
   ```env
   APP_NAME=YouTube AI Comment Agent
   DEBUG=False

   BACKEND_URL=http://localhost:8000
   FRONTEND_URL=http://localhost:8501

   # Google OAuth 2.0 Credentials
   GOOGLE_CLIENT_ID=your_client_id.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your_client_secret
   GOOGLE_REDIRECT_URI=http://localhost:8000/auth/callback
   GOOGLE_SCOPES=https://www.googleapis.com/auth/youtube.force-ssl

   # AI Provider: gemini | openai | ollama
   AI_PROVIDER=gemini
   GEMINI_API_KEY=your_gemini_api_key
   ```

---

## Running the Application

Open two terminals:

**Terminal 1 (FastAPI Backend):**
```bash
python -m uvicorn app.main:app --reload --port 8000
```

**Terminal 2 (Streamlit UI):**
```bash
python -m streamlit run streamlit_app.py
```
Streamlit interface will open at: `http://localhost:8501`.
