"""Command line use: python cli.py path/to/audio.mp3 [--language en] [--format json|srt|vtt|txt]"""
import argparse
import json
import logging

from app.formats import to_srt, to_txt, to_vtt
from app.pipeline import run_pipeline
from app.transcriber import get_transcriber

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

p = argparse.ArgumentParser(description="Transcribe an audio file with timestamps")
p.add_argument("path")
p.add_argument("--language", default=None)
p.add_argument("--words", action="store_true", help="include word timestamps")
p.add_argument("--format", default="json", choices=["json", "srt", "vtt", "txt"])
a = p.parse_args()

result = run_pipeline(a.path, get_transcriber(), a.language, a.words,
                      on_progress=lambda d, t: logging.info("chunk %d/%d done", d, t))
if a.format == "json":
    print(json.dumps(result, indent=2, ensure_ascii=False))
else:
    print({"srt": to_srt, "vtt": to_vtt, "txt": to_txt}[a.format](result["segments"]))
