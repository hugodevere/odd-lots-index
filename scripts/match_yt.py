"""Match Odd Lots podcast episodes (date, title) to YouTube videos by title.

Usage: python3 -I match_yt.py episodes.tsv playlists.tsv channel_videos.tsv out.csv

Inputs are UTF-8 tab-separated files without headers:
- episodes.tsv: publication date (YYYY-MM-DD), episode title
- playlists.tsv: kind (audio or video), YouTube video ID, video title
- channel_videos.tsv: YouTube video ID, video title (only Odd Lots titles used)
Prepare these metadata lists separately; this script does not fetch them.
"""
import csv
import difflib
import re
import sys


def norm(t):
    t = t.lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"\s*[|\-–—:]\s*(bloomberg\s+)?odd lots( podcast)?\s*$", "", t)
    t = re.sub(r"^odd lots\s*[:|\-–—]\s*", "", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def main():
    eps_path, pl_path, ch_path, out_path = sys.argv[1:5]
    episodes = [line.rstrip("\n").split("\t", 1) for line in open(eps_path, encoding="utf-8")]

    # candidates: (kind, id, title); playlist audio first, then playlist video, then channel uploads
    cands = []
    for line in open(pl_path, encoding="utf-8"):
        kind, vid, title = line.rstrip("\n").split("\t", 2)
        cands.append((kind, vid, title))
    for line in open(ch_path, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2 and "odd lots" in parts[1].lower():
            cands.append(("channel", parts[0], parts[1]))

    by_norm = {}
    for kind, vid, title in cands:
        by_norm.setdefault(norm(title), []).append((kind, vid, title))
    keys = list(by_norm)

    rows, found = [], 0
    for date, title in episodes:
        n = norm(title)
        hit, how = by_norm.get(n), "exact"
        if not hit:
            close = difflib.get_close_matches(n, keys, n=1, cutoff=0.88)
            hit, how = (by_norm[close[0]], "fuzzy") if close else (None, "")
        if hit:
            found += 1
            # prefer the full-audio playlist upload, then the video playlist, then any channel upload
            rank = {"audio": 0, "video": 1, "channel": 2}
            kind, vid, yt_title = sorted(hit, key=lambda h: rank[h[0]])[0]
            rows.append([date, title, f"https://www.youtube.com/watch?v={vid}", yt_title, how])
        else:
            rows.append([date, title, "", "", "not found"])

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "episode_title", "youtube_url", "youtube_title", "match"])
        w.writerows(rows)
    print(f"{found}/{len(episodes)} matched")


if __name__ == "__main__":
    main()
