"""The transcription pipeline: probe -> normalize -> split -> transcribe chunks -> merge."""
import logging
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .audio import probe_audio, normalize_audio, split_audio, Chunk
from .transcriber import Transcriber, RawSegment

log = logging.getLogger("pipeline")

CHUNK_SECONDS = 600     # 10 minute chunks for long files
OVERLAP_SECONDS = 1.0   # small overlap so edge words are not cut
MAX_CHUNK_RETRIES = 3


def to_segments(raw: list[RawSegment], offset: float = 0.0) -> list[dict]:
    """Clean JSON segments, with timestamps shifted to the original file's timeline."""
    out = []
    for s in raw:
        text = s.text.strip()
        if not text:
            continue
        seg = {"start": round(s.start + offset, 2), "end": round(s.end + offset, 2),
               "text": text, "confidence": round(s.confidence, 3)}
        if s.words:
            seg["words"] = [{**w, "start": round(w["start"] + offset, 2),
                             "end": round(w["end"] + offset, 2)} for w in s.words]
        out.append(seg)
    return out


def merge_chunks(parts: list[list[dict]], chunk_starts: list[float]) -> list[dict]:
    """Join chunk results in time order and drop segments repeated in the overlap.

    Chunk N also hears the first second of chunk N+1 (the overlap). Each segment is owned
    by the chunk whose window [chunk_start, next_chunk_start) contains its start time, so a
    segment that chunk N heard inside the overlap is dropped and chunk N+1's copy is kept.
    A sentence that starts just before the boundary stays with chunk N, which heard it fully.
    """
    merged: list[dict] = []
    for i, segs in enumerate(parts):
        window_end = chunk_starts[i + 1] if i + 1 < len(chunk_starts) else float("inf")
        for s in segs:
            if s["start"] >= window_end - 0.01:
                continue  # belongs to the next chunk
            if merged and s["start"] < merged[-1]["end"] - 0.5 and s["text"] == merged[-1]["text"]:
                continue  # same words seen twice across the boundary
            merged.append(s)
    merged.sort(key=lambda s: s["start"])
    for idx, s in enumerate(merged):
        s["id"] = idx
    return merged


def _transcribe_chunk(engine: Transcriber, chunk: Chunk, language, word_ts) -> tuple[list[dict], str]:
    """Transcribe one chunk, retrying only this chunk on failure (with backoff)."""
    last_err = None
    for attempt in range(1, MAX_CHUNK_RETRIES + 1):
        try:
            raw, lang = engine.transcribe(chunk.path, language, word_ts)
            return to_segments(raw, chunk.offset), lang
        except Exception as e:  # transient engine errors
            last_err = e
            log.warning("chunk at %.0fs failed (attempt %d): %s", chunk.offset, attempt, e)
            time.sleep(min(2 ** attempt, 10) * 0.1)
    raise RuntimeError(f"chunk at {chunk.offset:.0f}s failed after retries: {last_err}")


def run_pipeline(path: str, engine: Transcriber, language: str | None = None,
                 word_timestamps: bool = False, workers: int = 2,
                 on_progress: Callable[[int, int], None] | None = None) -> dict:
    """Run the full pipeline on one file and return the transcript."""
    started = time.time()
    info = probe_audio(path)
    work_dir = tempfile.mkdtemp(prefix="stt_")
    try:
        wav = normalize_audio(path, work_dir)
        chunks = split_audio(wav, work_dir, info.duration, CHUNK_SECONDS, OVERLAP_SECONDS)
        total, done = len(chunks), 0
        results: list = [None] * total
        # Chunks are independent, so they run in parallel. In production each chunk is a
        # Celery task in a group/chord; here a thread pool keeps the demo dependency-free.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_transcribe_chunk, engine, c, language, word_timestamps): i
                       for i, c in enumerate(chunks)}
            for fut, i in futures.items():
                results[i] = fut.result()
                done += 1
                if on_progress:
                    on_progress(done, total)
        segments = merge_chunks([r[0] for r in results], [c.offset for c in chunks])
        detected = next((r[1] for r in results if r[1]), language)
        return {
            "language": detected,
            "duration": round(info.duration, 2),
            "source": {"codec": info.codec, "container": info.container},
            "engine": engine.name,
            "chunks": total,
            "processing_seconds": round(time.time() - started, 2),
            "text": " ".join(s["text"] for s in segments),
            "segments": segments,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)  # always clean temp files
