"""Tests run with the mock engine, so no model download is needed (STT_ENGINE=mock)."""
import os
import subprocess
import time

import pytest

os.environ["STT_ENGINE"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from app import pipeline  # noqa: E402
from app.audio import AudioError, probe_audio  # noqa: E402
from app.formats import to_srt  # noqa: E402
from app.main import app  # noqa: E402
from app.transcriber import MockTranscriber  # noqa: E402

client = TestClient(app)


def make_audio(tmp_path, seconds, ext="mp3"):
    out = tmp_path / f"tone_{seconds}.{ext}"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration={seconds}",
                    "-ac", "2", "-ar", "44100", str(out)], check=True)
    return str(out)


@pytest.mark.parametrize("ext", ["mp3", "wav", "ogg", "m4a"])
def test_formats_are_normalized(tmp_path, ext):
    path = make_audio(tmp_path, 12, ext)
    result = pipeline.run_pipeline(path, MockTranscriber())
    assert result["chunks"] == 1
    assert abs(result["duration"] - 12) < 0.2
    assert result["segments"][0]["start"] == 0.0


def test_long_audio_is_chunked_with_correct_offsets(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "CHUNK_SECONDS", 20)
    path = make_audio(tmp_path, 50, "wav")
    result = pipeline.run_pipeline(path, MockTranscriber(segment_seconds=5))
    assert result["chunks"] == 3
    starts = [s["start"] for s in result["segments"]]
    assert starts == sorted(starts)                 # merged in time order
    assert max(s["end"] for s in result["segments"]) >= 49.5   # covers the whole file
    assert len(starts) == len(set(starts))          # overlap did not duplicate segments


def test_bad_file_is_rejected(tmp_path):
    bad = tmp_path / "notes.mp3"
    bad.write_text("this is not audio")
    with pytest.raises(AudioError):
        probe_audio(str(bad))


def test_api_sync(tmp_path):
    path = make_audio(tmp_path, 8)
    with open(path, "rb") as f:
        r = client.post("/v1/transcriptions", files={"file": ("a.mp3", f, "audio/mpeg")})
    assert r.status_code == 200
    body = r.json()
    assert body["segments"] and "start" in body["segments"][0]


def test_api_async_job_and_srt(tmp_path):
    path = make_audio(tmp_path, 8)
    with open(path, "rb") as f:
        r = client.post("/v1/transcriptions?sync=false", files={"file": ("a.mp3", f, "audio/mpeg")})
    job_id = r.json()["job_id"]
    for _ in range(50):
        job = client.get(f"/v1/transcriptions/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert job["status"] == "completed"
    srt = client.get(f"/v1/transcriptions/{job_id}?format=srt").text
    assert "-->" in srt


def test_api_rejects_unsupported_and_corrupt(tmp_path):
    r = client.post("/v1/transcriptions", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    r = client.post("/v1/transcriptions", files={"file": ("a.mp3", b"not audio", "audio/mpeg")})
    assert r.status_code == 400


def test_srt_format():
    srt = to_srt([{"start": 0, "end": 3.5, "text": "Hello"}])
    assert "00:00:00,000 --> 00:00:03,500" in srt
