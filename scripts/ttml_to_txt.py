"""Convert Apple Podcasts TTML transcripts to plain text, one paragraph per speaker turn.

Usage: python3 -I ttml_to_txt.py OUT_DIR "YYYY-MM-DD|Title|path.ttml" ...
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {"tt": "http://www.w3.org/ns/ttml"}
AGENT = "{http://www.w3.org/ns/ttml#metadata}agent"
UNIT = "{http://podcasts.apple.com/transcript-ttml-internal}unit"


def clock(t):
    # begin values look like "2.740", "41:58.540" or "1:02:03.000"
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    s = int(secs)
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def convert(path):
    root = ET.parse(path).getroot()
    turns = []  # [speaker, begin, [words]]
    for p in root.iter("{http://www.w3.org/ns/ttml}p"):
        words = [s.text for s in p.iter() if s.get(UNIT) == "word" and s.text]
        if not words:
            continue
        speaker = p.get(AGENT, "SPEAKER")
        if turns and turns[-1][0] == speaker:
            turns[-1][2].extend(words)
        else:
            turns.append([speaker, p.get("begin", "0"), words])
    return "\n\n".join(f"[{clock(b)}] {spk}: {' '.join(w)}" for spk, b, w in turns)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for arg in sys.argv[2:]:
        date, title, path = arg.split("|", 2)
        safe = re.sub(r'[\\/:*?"<>|]', "-", title).strip()
        dest = out / f"{date} Odd Lots - {safe}.txt"
        body = convert(path)
        dest.write_text(f"Odd Lots — {title}\nPublished {date}\n\n{body}\n", encoding="utf-8")
        print(f"{dest.name}\t{len(body.split())} words")


if __name__ == "__main__":
    main()
