"""Audio helpers built on FFmpeg: probe, normalize and split."""
import json
import os
import subprocess
from dataclasses import dataclass

TARGET_RATE = 16000  # Whisper expects 16 kHz mono


class AudioError(ValueError):
    """The file is not usable audio (corrupt, no audio stream, unsupported)."""


@dataclass
class AudioInfo:
    duration: float
    codec: str
    container: str


def probe_audio(path: str) -> AudioInfo:
    """Read the real format with ffprobe instead of trusting the file extension."""
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AudioError("Could not read the file. It may be corrupt or not a media file.")
    data = json.loads(r.stdout or "{}")
    audio = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        raise AudioError("The file has no audio stream.")
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise AudioError("The audio has zero length.")
    return AudioInfo(duration=duration,
                     codec=audio[0].get("codec_name", "unknown"),
                     container=data.get("format", {}).get("format_name", "unknown"))


def normalize_audio(path: str, out_dir: str) -> str:
    """Convert any input (mp3, m4a, ogg, video files...) to 16 kHz mono PCM WAV."""
    out = os.path.join(out_dir, "normalized.wav")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", path,
           "-vn",                      # drop video if present
           "-ac", "1",                 # mono
           "-ar", str(TARGET_RATE),    # 16 kHz
           "-c:a", "pcm_s16le", out]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise AudioError("Could not decode audio: " + r.stderr.strip()[-300:])
    return out


@dataclass
class Chunk:
    path: str
    offset: float  # start time of this chunk inside the original file (seconds)
    duration: float


def split_audio(wav_path: str, out_dir: str, total: float,
                chunk_seconds: float = 600, overlap: float = 1.0) -> list[Chunk]:
    """Cut a long WAV into chunks with a small overlap so words at the edges are not lost.

    Each chunk remembers its offset, which is later added to every segment timestamp.
    """
    if total <= chunk_seconds:
        return [Chunk(wav_path, 0.0, total)]
    chunks, start, i = [], 0.0, 0
    while start < total:
        length = min(chunk_seconds + overlap, total - start)
        out = os.path.join(out_dir, f"chunk_{i:03d}.wav")
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
               "-i", wav_path, "-c", "copy", out]  # WAV -> WAV, no re-encode
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise AudioError("Could not split audio: " + r.stderr.strip()[-300:])
        chunks.append(Chunk(out, start, length))
        start += chunk_seconds
        i += 1
    return chunks
