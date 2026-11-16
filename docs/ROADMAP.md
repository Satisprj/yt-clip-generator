# Development roadmap

Each phase ends with passing tests and a manual check, then waits for approval.

| Phase | Scope | Done when |
|-------|-------|-----------|
| **0. Foundation** ✅ | Folder structure, config, logging, error handling, DB session, Celery app (PostgreSQL broker on Windows), Claude CLI client, health endpoints, React + Tailwind shell | `pytest` passes; `/api/health/ready` reports database, queue, FFmpeg and Claude CLI as ok; the frontend shows backend status |
| **1. Jobs and video info** ✅ | `jobs` model + Alembic migration, YouTube URL validation, yt-dlp metadata extraction, `POST/GET /api/jobs`, Celery task that fetches info, frontend submit form + job page + history list | Submitting a real URL creates a job that shows the real title, thumbnail and duration |
| **2. Download and audio** ✅ | yt-dlp download (≤1080p, duration limit), FFmpeg audio extraction, progress updates, retry and failure handling | The job reaches `extracting_audio` complete with `source.mp4` + `audio.wav` on disk |
| **3. Transcription** ✅ | faster-whisper with word timestamps + VAD, sentence segmentation, `transcripts` table, transcript stored as JSON | A real video gets a sentence-level transcript with accurate times |
| **4. AI highlight detection** ✅ | Claude prompt + structured output (sentence-ID ranges, category, score, reason), boundary snapping to 20–30 s, overlap removal, ranking, titles and descriptions with Haiku | A job produces 5–10 ranked clip candidates with titles |
| **5. Clip rendering** ✅ | FFmpeg cut, OpenCV face tracking → smoothed 9:16 crop, word-level `.ass` subtitles burned in, `.srt` export, thumbnails | Playable vertical MP4s with captions |
| **6. Preview, download, history UI** | Clip gallery with video player, download buttons, live progress, history with delete and retry | The whole flow works end to end in the browser |
| **7. Hardening** | Disk cleanup policy, rate and duration limits, more tests, Dockerfiles for API and worker, README polish | Clean setup on a fresh machine |
