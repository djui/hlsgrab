#!/usr/bin/env python3
"""Download an HLS (.m3u8) stream with custom HTTP headers.

Usage:
  hlsgrab.py SOURCE -o out.mp4 [-H "Name: value"]... [-q best|worst|HEIGHT]

SOURCE is one of:
  - a playlist URL
  - a local .m3u8 file (needs --base-url)
  - a Chrome capture: HAR file, chrome://net-export NetLog (.json), or a
    "Copy as cURL" command (file, or "-" for stdin). The playlist URL and
    its request headers are taken from the capture.

Examples:
  hlsgrab.py https://host/path/index.m3u8 -o video.mp4 \
      -H "Referer: https://example.com/" -H "User-Agent: Mozilla/5.0"

  # Chrome capture: picks the first .m3u8 request and replays its headers
  hlsgrab.py capture.har -o video.mp4
  pbpaste | hlsgrab.py - -o video.mp4
  hlsgrab.py chrome-net-export-log.json --list

  # Best variant up to 720p
  hlsgrab.py capture.har -q 720 -o video.mp4

  # Local playlist file: segment URLs are resolved against --base-url
  hlsgrab.py index-v1-a1.m3u8 --base-url https://host/path/ -o video.mp4

Segments are fetched in parallel with retries and joined into one .ts
(or .mp4 for fMP4 playlists, with the EXT-X-MAP init segment first).
If ffmpeg is on PATH and the output has a different extension, the result
is remuxed (no re-encode) into that container. For master playlists,
--quality picks the variant (default: highest bandwidth). Encrypted
(EXT-X-KEY) streams are not supported.
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request


def fetch(url, headers, retries=5, timeout=30):
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            # Client errors won't fix themselves (except rate limiting), so don't retry them.
            permanent = isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500 and e.code != 429
            if attempt == retries or permanent:
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


# Captured headers that urllib manages itself or that would break a plain GET
# (e.g. accept-encoding would get us gzip bodies urllib doesn't decode).
SKIP_HEADERS = {"host", "content-length", "connection", "keep-alive", "accept-encoding",
                "range", "if-range", "if-none-match", "if-modified-since", "te", "upgrade",
                "priority"}


def is_playlist(url, content_type=""):
    return ".m3u8" in urllib.parse.urlsplit(url).path.lower() or "mpegurl" in content_type.lower()


def clean_headers(lines):
    """Turn captured "Name: value" lines into a header dict keyed by lowercase name."""
    out = {}
    for line in lines:
        name, sep, value = line.partition(":")  # pseudo-headers (":path: /x") get an empty name
        name, value = name.strip().lower(), value.strip()
        if not sep or not name or name in SKIP_HEADERS:
            continue
        if re.fullmatch(r"\[\d+ bytes were stripped\]", value):
            print(f"warning: capture has {name} redacted; record it with cookies/credentials "
                  "included if the download fails", file=sys.stderr)
            continue
        out[name] = f"{out[name]}; {value}" if name == "cookie" and name in out else value
    return out


def parse_curl(text):
    """Parse one or more Chrome "Copy as cURL (bash)" commands."""
    text = re.sub(r"\\\r?\n", " ", text)  # line continuations
    # bash $'...' strings -> plain shell quoting
    text = re.sub(r"\$'((?:[^'\\]|\\.)*)'", lambda m: shlex.quote(
        m.group(1).encode("latin-1", "backslashreplace").decode("unicode_escape")), text)
    out = []
    for cmd in text.splitlines():
        cmd = cmd.strip().rstrip(";").strip()
        if not cmd.startswith("curl "):
            continue
        url, lines, it = None, [], iter(shlex.split(cmd)[1:])
        for a in it:
            if a in ("-H", "--header"):
                lines.append(next(it, ""))
            elif a in ("-b", "--cookie"):
                lines.append("Cookie: " + next(it, ""))
            elif a in ("-A", "--user-agent"):
                lines.append("User-Agent: " + next(it, ""))
            elif a in ("-e", "--referer"):
                lines.append("Referer: " + next(it, ""))
            elif a == "--url":
                url = next(it, url)
            elif a in ("-X", "--request", "-d", "--data", "--data-raw", "--data-binary",
                       "--data-urlencode", "-o", "--output", "-u", "--user"):
                next(it, None)
            elif url is None and a.startswith(("http://", "https://")):
                url = a
        if url:
            out.append((url, lines))
    playlists = [c for c in out if is_playlist(c[0])]
    return playlists or out  # a single copied request may lack the .m3u8 extension


def parse_har(data):
    out = []
    for e in data["log"]["entries"]:
        req = e["request"]
        mime = e.get("response", {}).get("content", {}).get("mimeType", "")
        if is_playlist(req["url"], mime):
            out.append((req["url"], [f'{h["name"]}: {h["value"]}' for h in req.get("headers", [])]))
    return out


def parse_netlog(data):
    types = {v: k for k, v in data["constants"]["logEventTypes"].items()}
    urls, sent, ctype = {}, {}, {}
    for ev in data["events"]:
        t, sid, p = types.get(ev.get("type"), ""), ev.get("source", {}).get("id"), ev.get("params") or {}
        h = p.get("headers", [])
        if isinstance(h, dict):
            h = [f"{k}: {v}" for k, v in h.items()]
        if t == "URL_REQUEST_START_JOB" and "url" in p:
            urls[sid] = p["url"]  # last one wins, so redirects end at the final URL
        elif t.endswith("SEND_REQUEST_HEADERS"):  # HTTP/1, HTTP2_ and QUIC_ variants
            sent[sid] = h
        elif t == "HTTP_TRANSACTION_READ_RESPONSE_HEADERS":
            ctype[sid] = " ".join(x for x in h if x.lower().startswith("content-type"))
    return [(u, sent.get(sid, [])) for sid, u in urls.items() if is_playlist(u, ctype.get(sid, ""))]


def read_capture(src):
    """Return [(url, header_lines)] if src is a HAR, NetLog or cURL capture, else None."""
    if src.startswith(("http://", "https://")):
        return None
    if src == "-":
        text = sys.stdin.read()
    else:
        with open(src, encoding="utf-8", errors="replace") as f:
            text = f.read()
    s = text.lstrip()
    if s.startswith("#EXTM3U") and src != "-":
        return None
    if s.startswith("curl "):
        found = parse_curl(s)
    elif s.startswith("{"):
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            # net-export logs from a crashed or still-running browser lack the closing brackets
            data = json.loads(s.rstrip().rstrip(",") + "]}")
        if "log" in data:
            found = parse_har(data)
        elif "events" in data and "constants" in data:
            found = parse_netlog(data)
        else:
            sys.exit("error: JSON input is neither a HAR file nor a chrome://net-export log")
    else:
        sys.exit("error: SOURCE is not a URL, .m3u8 playlist, HAR, NetLog or cURL command")
    seen, out = set(), []
    for url, lines in found:
        if url not in seen:
            seen.add(url)
            out.append((url, lines))
    if not out:
        sys.exit("error: no .m3u8 request found in the capture (did playback start while recording?)")
    return out


def load_playlist(src, base_url, headers):
    if src.startswith(("http://", "https://")):
        return fetch(src, headers).decode("utf-8", "replace"), base_url or src
    with open(src, encoding="utf-8") as f:
        text = f.read()
    if not base_url:
        sys.exit("error: --base-url is required when reading a local playlist file")
    return text, base_url


def quality_arg(s):
    s = s.lower().rstrip("p")
    if s in ("best", "worst") or s.isdigit():
        return s
    raise argparse.ArgumentTypeError("expected best, worst or a height such as 720")


def pick_variant(variants, quality):
    """variants: [(height, bandwidth, url)]. quality: "best", "worst" or a max height."""
    if quality == "best":
        return max(variants)
    # Prefer variants with a known resolution: the rest are often audio-only.
    video = [v for v in variants if v[0]] or variants
    if quality == "worst":
        return min(video)
    fitting = [v for v in video if v[0] <= int(quality)]
    return max(fitting) if fitting else min(video)


def resolve(text, base, headers, quality="best"):
    """Return absolute segment URLs, following a master playlist if needed."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines or lines[0] != "#EXTM3U":
        sys.exit("error: not an M3U8 playlist")

    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        variants = []
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF:") and i + 1 < len(lines):
                attrs = parse_attrs(l.split(":", 1)[1])
                res = attrs.get("RESOLUTION", "")
                height = int(res.partition("x")[2]) if res.partition("x")[2].isdigit() else 0
                variants.append((height, int(attrs.get("BANDWIDTH", 0)), urllib.parse.urljoin(base, lines[i + 1])))
        chosen = pick_variant(variants, quality)
        print("master playlist variants:", file=sys.stderr)
        for v in sorted(variants, reverse=True):
            print(f"  {'*' if v == chosen else ' '} {f'{v[0]}p' if v[0] else '?':>6}  {v[1] / 1e6:6.2f} Mbps  {v[2]}",
                  file=sys.stderr)
        url = chosen[2]
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
    p.add_argument("source", help="playlist URL, local .m3u8 file, or Chrome capture (HAR, NetLog, cURL; - for stdin)")
    p.add_argument("-o", "--output", help="output file (.ts, or .mp4/.mkv with ffmpeg)")
    p.add_argument("-H", "--header", action="append", default=[], help='HTTP header, e.g. "Referer: https://x/" (repeatable)')
    p.add_argument("--base-url", help="base URL for relative segment paths (required for local files)")
    p.add_argument("-j", "--jobs", type=int, default=8, help="parallel downloads (default 8)")
    p.add_argument("--retries", type=int, default=5)
    p.add_argument("-q", "--quality", type=quality_arg, default="best",
                   help="master playlist variant: best (default), worst, or max height like 720")
    p.add_argument("--list", action="store_true", help="list the playlist requests found in a capture and exit")
    p.add_argument("--pick", type=int, default=1, metavar="N", help="use the N-th playlist request from a capture (default 1)")
    args = p.parse_args()
    if not args.output and not args.list:
        p.error("the following arguments are required: -o/--output")

    headers = {"user-agent": "Mozilla/5.0"}
    source = args.source
    capture = read_capture(source)
    if capture:
        for i, (url, _) in enumerate(capture, 1):
            print(f"{'*' if i == args.pick else ' '} {i}: {url}", file=sys.stderr)
        if args.list:
            return
        if not 1 <= args.pick <= len(capture):
            sys.exit(f"error: --pick must be between 1 and {len(capture)}")
        source, lines = capture[args.pick - 1]
        headers.update(clean_headers(lines))
        print(f"replaying headers: {', '.join(sorted(headers))}", file=sys.stderr)
    elif args.list:
        sys.exit("error: --list needs a HAR, NetLog or cURL capture as SOURCE")
    for h in args.header:
        name, sep, value = h.partition(":")
        if not sep:
            sys.exit(f"error: bad header {h!r}, expected 'Name: value'")
        headers[name.strip().lower()] = value.strip()

    text, base = load_playlist(source, args.base_url, headers)
    init, segments = resolve(text, base, headers, args.quality)
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
    try:
        main()
    except RuntimeError as e:
        hint = " (missing or expired headers/cookies? capture again)" if "HTTP Error 403" in str(e) else ""
        sys.exit(f"\nerror: {e}{hint}")
    except KeyboardInterrupt:
        sys.exit(130)
