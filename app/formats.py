"""Export a transcript as plain text, SRT or WebVTT."""


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments: list[dict]) -> str:
    blocks = [f"{i}\n{_ts(s['start'], ',')} --> {_ts(s['end'], ',')}\n{s['text']}\n"
              for i, s in enumerate(segments, 1)]
    return "\n".join(blocks)


def to_vtt(segments: list[dict]) -> str:
    lines = ["WEBVTT", ""]
    for s in segments:
        lines += [f"{_ts(s['start'], '.')} --> {_ts(s['end'], '.')}", s["text"], ""]
    return "\n".join(lines)


def to_txt(segments: list[dict]) -> str:
    return "\n".join(f"[{_ts(s['start'], '.')}] {s['text']}" for s in segments)
