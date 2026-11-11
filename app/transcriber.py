"""Speech-to-text engines behind one small interface.

The rest of the pipeline only knows about `Transcriber.transcribe()`, so the engine can be
swapped (faster-whisper locally, a hosted API, or a mock in tests) without other changes.
"""
import os
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RawSegment:
    start: float
    end: float
    text: str
    confidence: float = 0.0
    words: list = field(default_factory=list)


class Transcriber(Protocol):
    name: str

    def transcribe(self, wav_path: str, language: str | None = None,
                   word_timestamps: bool = False) -> tuple[list[RawSegment], str]:
        ...


class FasterWhisperTranscriber:
    """Open-source Whisper on CTranslate2. Runs on CPU (int8) or GPU (float16)."""

    def __init__(self, model_size: str = "small", device: str = "cpu",
                 compute_type: str = "int8"):
        from faster_whisper import WhisperModel  # imported lazily: heavy dependency
        self.name = f"faster-whisper:{model_size}"
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, wav_path, language=None, word_timestamps=False):
        segments, info = self.model.transcribe(
            wav_path,
            language=language,          # None = auto detect
            vad_filter=True,            # skip silence, fewer hallucinated words
            beam_size=5,
            word_timestamps=word_timestamps,
        )
        out = []
        for s in segments:  # generator: decoding happens while we iterate
            words = [{"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word}
                     for w in (s.words or [])]
            out.append(RawSegment(s.start, s.end, s.text, s.avg_logprob, words))
        return out, info.language


class MockTranscriber:
    """Deterministic fake engine for tests and local demos without a model download."""

    name = "mock"

    def __init__(self, segment_seconds: float = 5.0):
        self.segment_seconds = segment_seconds

    def transcribe(self, wav_path, language=None, word_timestamps=False):
        from .audio import probe_audio
        total = probe_audio(wav_path).duration
        out, t, i = [], 0.0, 0
        while t < total - 0.01:
            end = min(t + self.segment_seconds, total)
            out.append(RawSegment(t, end, f"mock segment {i}", -0.1))
            t, i = end, i + 1
        return out, language or "en"


def get_transcriber() -> Transcriber:
    engine = os.getenv("STT_ENGINE", "faster-whisper")
    if engine == "mock":
        return MockTranscriber()
    return FasterWhisperTranscriber(
        model_size=os.getenv("WHISPER_MODEL", "small"),
        device=os.getenv("WHISPER_DEVICE", "cpu"),
        compute_type=os.getenv("WHISPER_COMPUTE", "int8"),
    )
