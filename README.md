# Transcription Pipeline

A small service that takes an audio file (WAV, MP3, M4A, OGG, FLAC, WEBM, or even a video file), turns the speech into text, and returns the transcript with start and end times for every segment. It handles long recordings by cutting them into chunks, transcribing the chunks in parallel, and joining them back with the correct timestamps.

The focus is on the engineering around the model: format handling, long files, failures and a clean API. The model itself is open-source Whisper, run through [faster-whisper](https://github.com/SYSTRAN/faster-whisper).

## Quick start

Requirements: Python 3.10+ and FFmpeg (`brew install ffmpeg`, `sudo apt install ffmpeg`, or `choco install ffmpeg`).

```bash
pip install -r requirements.txt

# Command line
python cli.py samples/meeting.mp3                 # JSON with segments
python cli.py samples/meeting.mp3 --format srt    # subtitles

# API
uvicorn app.main:app --reload
# open http://localhost:8000/docs for the interactive Swagger page
curl -F "file=@samples/meeting.mp3" http://localhost:8000/v1/transcriptions
```

On Windows you can also double-click `setup_windows.bat` (installs FFmpeg with winget, creates a virtual env, installs packages and runs the tests), then drag an audio file onto `transcribe.bat`. It saves a JSON transcript and an SRT file next to the audio.

The first run downloads the Whisper model (`small` by default, about 460 MB). Set `WHISPER_MODEL=tiny` for a fast try, or `base`, `medium` or `large-v3` for more accuracy.

Run the tests (they use a mock engine, so no model download is needed):

```bash
pytest -q
```

Docker:

```bash
docker build -t transcription-pipeline .
docker run -p 8000:8000 transcription-pipeline
```

## How it works

```
upload ──> probe (ffprobe) ──> normalize (ffmpeg: 16 kHz mono WAV)
                                   │
                         short file│long file
                                   ▼
                     split into 10 min chunks (+1 s overlap)
                                   │
              transcribe chunks in parallel (faster-whisper + VAD)
                                   │
          shift timestamps by chunk offset ──> merge + remove overlap
                                   │
                    JSON segments / full text / SRT / VTT / TXT
```

| File | What it does |
|---|---|
| `app/audio.py` | `probe_audio` checks the real format with ffprobe. `normalize_audio` converts anything to 16 kHz mono WAV. `split_audio` cuts long files into chunks and records each chunk's offset. |
| `app/transcriber.py` | One small `Transcriber` interface with two engines: `FasterWhisperTranscriber` (real) and `MockTranscriber` (tests and demos). |
| `app/pipeline.py` | `run_pipeline` runs the steps, transcribes chunks in parallel, retries a failed chunk, merges results and always cleans up temp files. |
| `app/main.py` | FastAPI app: upload, sync or async processing, job status, export formats. |
| `app/jobs.py` | A simple job store and worker pool for long files (in memory, so the project runs with no extra services). |
| `app/formats.py` | SRT, VTT and TXT export. |
| `cli.py` | Command line entry point. |
| `tests/` | Format handling, chunking and offsets, bad files, sync and async API. |

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/transcriptions` | Upload a file (`multipart/form-data`, field `file`). Query: `language` (empty = auto detect), `word_timestamps`, `sync`. Files up to 5 minutes return the transcript directly. Longer files return a job id right away, like `{job_id, status: "queued"}`. |
| `GET` | `/v1/transcriptions/{id}` | Job status, progress (`done` / `total` chunks) and the result when finished. Add `?format=srt`, `vtt` or `txt` to export. |
| `GET` | `/health` | Health check. |

Example response:

```json
{
  "language": "en",
  "duration": 62.4,
  "engine": "faster-whisper:small",
  "chunks": 1,
  "text": "Hello everyone, welcome to the weekly sync. Let's start with updates.",
  "segments": [
    {"id": 0, "start": 0.0,  "end": 3.84, "text": "Hello everyone, welcome to the weekly sync.", "confidence": -0.21},
    {"id": 1, "start": 3.84, "end": 6.1,  "text": "Let's start with updates.", "confidence": -0.18}
  ]
}
```

Errors are clear: `415` unsupported file type, `413` file too large, `400` corrupt file or no audio stream.

## Design decisions

**1. faster-whisper as the engine.** Whisper is accurate, handles many languages with automatic detection, and is open source. faster-whisper runs it up to about 4x faster with less memory, works on CPU with int8 and on GPU, and has voice activity detection (VAD) built in. VAD skips silence, which saves time and reduces made-up words in quiet parts. The model is loaded once per process, never per request.

**2. One interface for the engine.** The pipeline only calls `Transcriber.transcribe()`. Switching to a hosted API (OpenAI, Deepgram, AssemblyAI) for speed or speaker labels, or to a mock in tests, does not touch the rest of the code.

**3. Normalize every input first.** FFmpeg converts any container or codec to 16 kHz, mono, 16-bit WAV, which is what Whisper expects. Video files work too (`-vn` drops the video). The real format is checked with ffprobe, not the file extension, so a renamed text file is rejected with a `400` instead of crashing a worker.

**4. Long files are chunked.** One blocking call on a 2-hour file would time out and use a lot of memory. The audio is cut into 10-minute chunks with a 1-second overlap, using `-c copy` so splitting is fast. Each chunk keeps its offset in the original file, and every segment time becomes `chunk_offset + segment_time`.

**5. Removing the overlap.** Each chunk owns the time window from its own start to the next chunk's start. A segment heard in the overlap at the end of chunk N is dropped because chunk N+1 owns it. A sentence that starts just before a boundary stays with chunk N, which heard all of it. The test suite checks that timestamps are in order, cover the whole file, and are not duplicated. Writing that test caught a real duplication bug in my first version of the merge.

**6. Parallel chunks.** Chunks do not depend on each other, so they run at the same time. Here a thread pool does it. In production each chunk would be a Celery task in a `chord`, so many workers or GPUs can share one long file.

**7. Sync for short files, async jobs for long ones.** Short files (5 minutes or less) get the answer in the same request, which is simplest for the client. Longer files return a `job_id` right away, and the client polls the status (with progress) or, in production, gets a webhook or WebSocket event.

**8. Failures.** A failed chunk is retried on its own with backoff, so one bad chunk does not restart a 2-hour file. Permanent errors (corrupt file, no audio) are not retried and return a clear reason. Temp files are always deleted in a `finally` block. Uploads are streamed to disk in 1 MB pieces with a size limit, so a large upload never sits in memory.

## Production version (not implemented here)

What I would change to run this for real users:

- **Concurrent uploads.** Clients upload large files straight to S3 with a pre-signed URL. The API only creates a job row and puts a message on a queue (Celery + Redis or SQS), so it stays fast and scales horizontally. GPU workers pull from the queue, use `prefetch=1`, and autoscale on queue length. Separate queues and per-user limits stop one big batch from blocking everyone. An `Idempotency-Key` header and a file hash prevent duplicate jobs.
- **Storage.** Audio in private S3 (`tenant_id/job_id/...`), encrypted, with lifecycle rules that delete chunks after a day and originals after the agreed retention. Jobs and segments in PostgreSQL (`jobs`, `transcript_segments` with start, end, text, confidence), with full-text search on the text. The model version is saved with every transcript.
- **Recovery.** Celery `acks_late` so a crashed worker's task goes back to the queue, idempotent chunk tasks, exponential backoff with a retry limit, a watchdog that finds jobs stuck in `processing`, a dead-letter queue with alerts, and a manual retry endpoint.
- **API.** Versioned REST as above, plus `POST /v1/uploads` for pre-signed URLs, signed webhooks when a job finishes, API key or JWT auth tied to a tenant, rate limits, and metrics (queue length, processing time per audio minute, error rate).
- **Quality.** Track word error rate on a small labelled test set when changing the model or settings, and add speaker diarization (for example pyannote) if speaker labels are needed.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `STT_ENGINE` | `faster-whisper` | `faster-whisper` or `mock` |
| `WHISPER_MODEL` | `small` | `tiny`, `base`, `small`, `medium`, `large-v3` |
| `WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` |
| `WHISPER_COMPUTE` | `int8` | `int8` on CPU, `float16` on GPU |
| `SYNC_LIMIT_SECONDS` | `300` | Files longer than this run as background jobs |
| `MAX_UPLOAD_MB` | `500` | Upload size limit |
| `JOB_WORKERS` | `2` | Background job workers |
