# hlsgrab

Download an HLS (`.m3u8`) video stream to a single file, with the same HTTP
headers your browser sent. It's one Python file with no third-party packages.

Some sites only serve their HLS segments when the request carries the right
`Referer`, `Origin`, `Cookie` or `User-Agent` headers. Generic downloaders then
fail with `403 Forbidden`. `hlsgrab` lets you copy those headers from Chrome
DevTools and replay them for every playlist and segment request.

```bash
./hlsgrab.py "https://cdn.example.com/video/index.m3u8" -o video.mp4 \
    -H "Referer: https://example.com/" \
    -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
```

## Features

- **Master playlists:** automatically picks the highest-bandwidth variant.
- **Parallel downloads:** segments are fetched in parallel (`-j`, default 8),
  with exponential-backoff retries (`--retries`, default 5).
- **MPEG-TS and fMP4 playlists:** for fMP4, the `EXT-X-MAP` init segment is
  written first.
- **Optional remux with `ffmpeg`:** the result can be repackaged into `.mp4` or
  `.mkv` with `-c copy`, so nothing is re-encoded and there's no quality loss.
- **Local playlist files:** handy when the playlist URL is short-lived but you
  saved the `.m3u8` body. Pass `--base-url` so relative segment paths resolve.

## Requirements

| Dependency | Required | Purpose |
|---|---|---|
| Python ≥ 3.8 | yes | runs the script (standard library only) |
| [ffmpeg](https://ffmpeg.org/) | optional | remuxes to a container other than the native `.ts` / `.mp4` |

Without `ffmpeg`, the output gets the stream's native extension: `.ts` for
MPEG-TS playlists, `.mp4` for fMP4 playlists. For example, `-o video.mp4` on an
MPEG-TS stream writes `video.ts` and prints a note saying so.

## Installation

```bash
git clone https://github.com/djui/hlsgrab.git
cd hlsgrab
./hlsgrab.py --help
```

To run it from anywhere, put it on your `PATH`:

```bash
ln -s "$PWD/hlsgrab.py" /usr/local/bin/hlsgrab
```

Optional, to install ffmpeg:

```bash
brew install ffmpeg        # macOS
sudo apt install ffmpeg    # Debian/Ubuntu
```

## Usage

```
hlsgrab.py SOURCE -o OUTPUT [-H "Name: value"]... [--base-url URL] [-j N] [--retries N]
```

| Option | Description |
|---|---|
| `SOURCE` | Playlist URL (`http(s)://…`) or path to a local `.m3u8` file |
| `-o, --output` | Output file. `.ts` or `.mp4` match the stream's native format; anything else is remuxed with ffmpeg |
| `-H, --header` | Extra HTTP header, `"Name: value"`. Repeatable. Overrides the default `User-Agent: Mozilla/5.0` |
| `--base-url` | Base URL for relative segment paths. Required for local files |
| `-j, --jobs` | Parallel segment downloads (default 8) |
| `--retries` | Attempts per segment before giving up (default 5) |

### Examples

Remote playlist with headers:

```bash
./hlsgrab.py "https://cdn.example.com/hls/master.m3u8" -o talk.mp4 \
    -H "Referer: https://example.com/watch/123" \
    -H "Origin: https://example.com"
```

Local playlist saved from DevTools (Network → playlist request → Response →
save the body):

```bash
./hlsgrab.py index-v1-a1.m3u8 --base-url "https://cdn.example.com/hls/abc/" -o talk.mkv
```

Go easier on the server:

```bash
./hlsgrab.py "$URL" -o out.mp4 -j 2 --retries 10
```

## Capturing the request in Chrome

1. Open the page with the video in Chrome.
2. Open DevTools: **⌥⌘I** (macOS) or **Ctrl+Shift+I** (Windows/Linux), then go
   to the **Network** tab.
3. In the filter box, type `m3u8`. Leave **Preserve log** on so that redirects
   don't clear the list.
4. **Reload** the page and **start playback**. One or more `.m3u8` requests
   appear:
   - A *master* playlist, often `master.m3u8` or `playlist.m3u8`, which lists
     quality variants. Pass this one and `hlsgrab` picks the best variant.
   - *Media* playlists, often `index-v1-a1.m3u8` or `chunklist_…m3u8`, which
     list the segments. Passing one of these pins a specific quality.
5. Click the request and open the **Headers** tab:
   - **General → Request URL** is the `SOURCE` argument.
   - **Request Headers** lists the headers to replay. Usually `Referer`, and
     sometimes `Origin`, `Cookie`, `Authorization` or `User-Agent`, are what
     matter. Pass each one with `-H`.
6. Quicker option: right-click the request and choose **Copy → Copy as cURL**.
   The copied command holds the URL and every header (`-H '…'`). Copy them
   into an `hlsgrab` command line.

Tips:

- If the playlist URL contains a token or expiry
  (`?token=…&expires=…`), run `hlsgrab` soon after capturing it.
- If the playlist fetch works but segments return 403, compare a segment
  request (filter `.ts` or `.m4s`) with the playlist request. Segments
  sometimes need different headers or cookies.
- If the playlist URL has expired, save the playlist body from the
  **Response** tab and use the local-file form with `--base-url` set to the
  directory URL of the original playlist. The segment URLs themselves must
  still be valid.
- Treat copied `Cookie` and `Authorization` values as passwords. Don't commit
  them or paste them into shared shell history.

### Without DevTools: `chrome://net-export`

DevTools can be unavailable, for example when it's disabled by an enterprise
policy or a page closes or blanks itself as soon as DevTools opens. In that
case you can record a *NetLog* instead. Chrome writes it from the network
stack itself, so the page can't see or interfere with it.

1. Open a new tab at `chrome://net-export`.
2. Under **Options**, pick a privacy level:
   - **Strip private information** (default): records URLs, but redacts
     cookies and credentials. Use this when only `Referer`, `Origin` or
     `User-Agent` matter.
   - **Include cookies and credentials**: needed if the stream requires a
     `Cookie` or `Authorization` header.
   - Leave **Include raw bytes** off. It isn't needed and makes the file huge.
3. Click **Start Logging to Disk** and choose where to save the `.json` file.
4. Switch to the video tab, **reload** it and **play** for a few seconds.
5. Go back to the net-export tab and click **Stop Logging**.

Then get the playlist URL and headers out of the log. The quickest way is
from the shell:

```bash
# Playlist URLs
grep -oE 'https?://[^"]+\.m3u8[^"]*' chrome-net-export-log.json | sort -u

# Request headers that are usually needed (case-insensitive; HTTP/2 and
# HTTP/3 logs use lowercase names)
grep -oiE '"(referer|origin|cookie|authorization|user-agent): [^"]*"' \
    chrome-net-export-log.json | sort -u
```

To see the headers next to a specific request, use the
[NetLog Viewer](https://netlog-viewer.appspot.com/) instead:

1. Open the viewer and load the `.json` file under **Import**.
2. Open the **Events** tab and type `m3u8` in the filter box.
3. Click a `URL_REQUEST` entry. The headers sent are listed under
   `HTTP_TRANSACTION_SEND_REQUEST_HEADERS`. For HTTP/2 and HTTP/3 they appear
   under `…_HTTP2_SEND_REQUEST_HEADERS` and `…_QUIC_SEND_REQUEST_HEADERS`
   instead.

Notes:

- Administrators can block `chrome://net-export` too, for example through
  the `URLBlocklist` policy. If both are blocked, use another browser or a
  proxy such as [mitmproxy](https://mitmproxy.org/).
- A log made with **Include cookies and credentials** holds live session
  tokens for *every* site you visited while logging. Log as briefly as you can
  (ideally in a fresh window), don't share the file, and delete it when you're
  done.

## Tradeoffs and limitations

- **No encrypted streams.** Playlists with `EXT-X-KEY` (AES-128, SAMPLE-AES)
  are refused, and DRM (Widevine, FairPlay, PlayReady) can't be handled.
- **VOD only.** The playlist is read once. Live or event playlists that keep
  growing are not followed.
- **The highest bandwidth always wins.** There is no flag to choose a lower
  variant. Pass that variant's media playlist directly instead.
- **No separate audio or subtitle renditions.** `EXT-X-MEDIA` alternate audio
  and subtitle tracks are ignored. If a site puts audio in its own rendition,
  the output will have no sound.
- **No byte ranges.** `EXT-X-BYTERANGE` segments and `EXT-X-MAP` with
  `BYTERANGE` are not supported.
- **Disk use.** Segments are buffered in a temporary directory and then
  concatenated, so you need about twice the video's size in free space while it
  runs. In exchange, parallel fetching and retries are simple.
- **Discontinuities.** Segments are joined byte-for-byte. Streams with
  `EXT-X-DISCONTINUITY` (inserted ads, mixed encodings) may give timestamp
  glitches. Remuxing through ffmpeg usually smooths these over.
- **Basic retry logic.** Any error, including a 403 or 404, is retried with
  backoff up to `--retries` times. A bad header therefore takes a while to fail
  instead of failing fast.

### Why not `ffmpeg -i URL` or `yt-dlp`?

ffmpeg can read HLS directly and accepts headers via `-headers`, but it fetches
segments one at a time. yt-dlp is far more capable (encryption, live streams,
separate audio, site extractors). It's also a large dependency, and it can be
overkill when you already have the exact playlist URL and headers from
DevTools. `hlsgrab` is the small, readable middle ground: easy to audit and
easy to modify.

## Legal

Only download content you have the right to download. This tool does not
bypass DRM or encryption. Respect the terms of service of the sites you use it
with.

## License

[MIT](LICENSE) © 2026 Uwe Dauernheim
