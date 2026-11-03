#!/usr/bin/env python3
"""Download an HLS (.m3u8) stream with custom HTTP headers.

Usage:
  hlsgrab.py URL_OR_FILE -o out.mp4 [-H "Name: value"]... [--base-url URL]

Examples:
  hlsgrab.py https://host/path/index.m3u8 -o video.mp4 \
      -H "Referer: https://example.com/" -H "User-Agent: Mozilla/5.0"

  # Local playlist file: segment URLs are resolved against --base-url
  hlsgrab.py index-v1-a1.m3u8 --base-url https://host/path/ -o video.mp4

Segments are fetched in parallel with retries and joined into one .ts
(or .mp4 for fMP4 playlists, with the EXT-X-MAP init segment first).
If ffmpeg is on PATH and the output has a different extension, the result
is remuxed (no re-encode) into that container. Master playlists pick the
highest-bandwidth variant. Encrypted (EXT-X-KEY) streams are not supported.
"""
import argparse
import concurrent.futures as cf
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request


def fetch(url, headers, retries=5, timeout=30):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if attempt == retries:
                raise RuntimeError(f"{url}: {e}") from e
            time.sleep(min(2 ** attempt, 30))


def parse_attrs(s):
    out, key, quoted, buf = {}, None, False, ""
    for ch in s + ",":
        if ch == '"':
            quoted = not quoted
        elif ch == "=" and not quoted and key is None:
            key, buf = buf, ""
        elif ch == "," and not quoted:
            if key is not None:
                out[key.strip()] = buf.strip()
            key, buf = None, ""
        else:
            buf += ch
    return out


def load_playlist(src, base_url, headers):
    if src.startswith(("http://", "https://")):
        return fetch(src, headers).decode("utf-8", "replace"), base_url or src
    with open(src, encoding="utf-8") as f:
        text = f.read()
    if not base_url:
        sys.exit("error: --base-url is required when reading a local playlist file")
    return text, base_url


def resolve(text, base, headers):
    """Return absolute segment URLs, following a master playlist if needed."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines or lines[0] != "#EXTM3U":
        sys.exit("error: not an M3U8 playlist")

    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        variants = []
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF:") and i + 1 < len(lines):
                bw = int(parse_attrs(l.split(":", 1)[1]).get("BANDWIDTH", 0))
                variants.append((bw, urllib.parse.urljoin(base, lines[i + 1])))
        bw, url = max(variants)
        print(f"master playlist: picking variant {bw} bps -> {url}", file=sys.stderr)
        return resolve(fetch(url, headers).decode("utf-8", "replace"), url, headers)

    init = None
    for l in lines:
        if l.startswith("#EXT-X-KEY") and "METHOD=NONE" not in l:
            sys.exit("error: encrypted playlists (EXT-X-KEY) are not supported")
        if l.startswith("#EXT-X-MAP:"):
            attrs = parse_attrs(l.split(":", 1)[1])
            if "BYTERANGE" in attrs:
                sys.exit("error: EXT-X-MAP with BYTERANGE is not supported")
            init = urllib.parse.urljoin(base, attrs["URI"])
    return init, [urllib.parse.urljoin(base, l) for l in lines if not l.startswith("#")]


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="playlist URL or local .m3u8 file")
    p.add_argument("-o", "--output", required=True, help="output file (.ts, or .mp4/.mkv with ffmpeg)")
    p.add_argument("-H", "--header", action="append", default=[], help='HTTP header, e.g. "Referer: https://x/" (repeatable)')
    p.add_argument("--base-url", help="base URL for relative segment paths (required for local files)")
    p.add_argument("-j", "--jobs", type=int, default=8, help="parallel downloads (default 8)")
    p.add_argument("--retries", type=int, default=5)
    args = p.parse_args()

    headers = {"User-Agent": "Mozilla/5.0"}
    for h in args.header:
        name, sep, value = h.partition(":")
        if not sep:
            sys.exit(f"error: bad header {h!r}, expected 'Name: value'")
        headers[name.strip()] = value.strip()

    text, base = load_playlist(args.source, args.base_url, headers)
    init, segments = resolve(text, base, headers)
    if not segments:
        sys.exit("error: playlist has no segments")
    print(f"{len(segments)} segments" + (" (fMP4)" if init else ""), file=sys.stderr)
    ext = ".mp4" if init else ".ts"

    with tempfile.TemporaryDirectory(prefix="hlsgrab_") as tmp:
        parts = [f"{i:06d}.seg" for i in range(len(segments))]
        if init:
            with open(os.path.join(tmp, "init.seg"), "wb") as f:
                f.write(fetch(init, headers, retries=args.retries))
            parts.insert(0, "init.seg")

        def get(i_url):
            i, url = i_url
            with open(os.path.join(tmp, f"{i:06d}.seg"), "wb") as f:
                f.write(fetch(url, headers, retries=args.retries))

        done = 0
        with cf.ThreadPoolExecutor(max_workers=args.jobs) as ex:
            for _ in ex.map(get, enumerate(segments)):
                done += 1
                print(f"\r{done}/{len(segments)}", end="", file=sys.stderr, flush=True)
        print(file=sys.stderr)

        joined = os.path.join(tmp, "joined" + ext)
        with open(joined, "wb") as out:
            for name in parts:
                with open(os.path.join(tmp, name), "rb") as f:
                    shutil.copyfileobj(f, out)

        if args.output.lower().endswith(ext):
            shutil.move(joined, args.output)
        elif shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", joined,
                            "-c", "copy", args.output], check=True)
        else:
            raw_out = os.path.splitext(args.output)[0] + ext
            print(f"ffmpeg not found; writing {raw_out} instead", file=sys.stderr)
            shutil.move(joined, raw_out)
            args.output = raw_out

    print(f"saved {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
