# hlsgrab

Download an HLS (`.m3u8`) video stream to a single file, with the same HTTP
headers your browser sent. It's one Python file with no third-party packages.

Some sites only serve their HLS segments when the request carries the right
`Referer`, `Origin`, `Cookie` or `User-Agent` headers. Generic downloaders then
fail with `403 Forbidden`. Give `hlsgrab` a Chrome network capture (HAR
export, `chrome://net-export` log or "Copy as cURL"). It finds the playlist
request in the capture and replays that request's headers for every playlist
and segment request.

```bash
# From a capture (no copying of URLs or headers by hand)
./hlsgrab.py capture.har -o video.mp4

# Or with the URL and headers spelled out
./hlsgrab.py "https://cdn.example.com/video/index.m3u8" -o video.mp4 \
    -H "Referer: https://example.com/" \
    -H "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
```

## Features

- **Chrome captures as input:** reads a HAR file, a `chrome://net-export`
  NetLog, or one or more "Copy as cURL" commands from a file or stdin. It
  finds the `.m3u8` request and reuses its URL and headers. Headers that would
  break a plain download, such as `accept-encoding` or `range`, are dropped.
- **Master playlists:** picks a variant with `-q best|worst|HEIGHT` (default
  `best`) and prints every variant it found.
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
hlsgrab.py SOURCE -o OUTPUT [-q best|worst|HEIGHT] [-H "Name: value"]... [--pick N] [--list]
                            [--base-url URL] [-j N] [--retries N]
```

| Option | Description |
|---|---|
| `SOURCE` | Playlist URL, local `.m3u8` file, or Chrome capture: `.har`, net-export `.json`, or a file of `curl …` commands. `-` reads the capture from stdin |
| `-o, --output` | Output file. `.ts` or `.mp4` match the stream's native format; anything else is remuxed with ffmpeg |
| `-q, --quality` | Master playlist variant: `best` (default), `worst`, or a maximum height such as `720` / `720p`, which picks the best variant at or below it, or the smallest one if none fits |
| `-H, --header` | Extra HTTP header, `"Name: value"`. Repeatable. Overrides captured headers and the default `User-Agent: Mozilla/5.0` |
| `--list` | List the playlist requests found in a capture and exit |
| `--pick N` | Use the N-th playlist request from the capture (default 1, the earliest) |
| `--base-url` | Base URL for relative segment paths. Required for local `.m3u8` files |
| `-j, --jobs` | Parallel segment downloads (default 8) |
| `--retries` | Attempts per request for network errors and 5xx/429 responses (default 5). Other 4xx responses fail immediately |

### Examples

From a Chrome capture:

```bash
./hlsgrab.py capture.har -o video.mp4                    # HAR export
./hlsgrab.py chrome-net-export-log.json -o video.mp4     # chrome://net-export
pbpaste | ./hlsgrab.py - -o video.mp4                    # "Copy as cURL" on the clipboard (macOS)
```

If the capture holds several playlists (an ad pre-roll, separate audio and
video media playlists, …), list them and pick one:

```bash
./hlsgrab.py capture.har --list
#  * 1: https://ads.example.net/preroll/master.m3u8
#    2: https://cdn.example.com/hls/abc/master.m3u8
./hlsgrab.py capture.har --pick 2 -o video.mp4
```

Choose the quality:

```bash
./hlsgrab.py capture.har -q 720 -o video.mp4    # best variant ≤ 720p
./hlsgrab.py capture.har -q worst -o small.mp4
# master playlist variants:
#      1080p    5.00 Mbps  https://cdn.example.com/hls/abc/v1080/index.m3u8
#    *  720p    2.80 Mbps  https://cdn.example.com/hls/abc/v720/index.m3u8
#       360p    0.80 Mbps  https://cdn.example.com/hls/abc/v360/index.m3u8
```

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

### Quickest: hand the capture to `hlsgrab`

1. Open the video page, open DevTools (**⌥⌘I** or **Ctrl+Shift+I**), and go to
   the **Network** tab.
2. **Reload** the page and **start playback**.
3. Do one of the following:
   - **HAR:** click the **Export HAR** (download arrow) button in the Network
     toolbar and save `capture.har`. Recent Chrome versions export a
     *sanitized* HAR without cookies by default. If the site needs cookies,
     enable **Settings → Preferences → Network → Allow to generate HAR with
     sensitive data** and choose **Export HAR (with sensitive data)**.
   - **cURL:** type `m3u8` in the filter box, right-click the playlist request,
     and choose **Copy → Copy as cURL (bash)**, or **Copy all as cURL (bash)**
     to take every listed request. Then run `pbpaste | ./hlsgrab.py - -o
     video.mp4`, or save the text to a file and pass that.
4. Run `./hlsgrab.py capture.har -o video.mp4`.

If DevTools is blocked, record a `chrome://net-export` log (see
[below](#without-devtools-chromenet-export)) and pass the `.json` file in the
same way.

### By hand

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
6. Run `./hlsgrab.py chrome-net-export-log.json -o video.mp4`. If the log was
   recorded with **Strip private information**, `hlsgrab` warns that the
   cookies were redacted.

To inspect the log yourself instead, use the shell:

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
- **Quality is chosen by height only.** `-q` compares `RESOLUTION` heights,
  using bandwidth to break ties. Codec (H.264 vs HEVC), frame rate and
  HDR are not considered. To get one exact variant, pass its media playlist
  (shown in the variant list) as `SOURCE`.
- **One set of headers for everything.** The headers of the captured playlist
  request are reused for the variant playlist and every segment. If a CDN
  requires different headers per host, override them with `-H`.
- **Captures expire.** A HAR or NetLog file holds the headers and cookies as
  they were when it was recorded. Signed URLs and session cookies stop working
  eventually, so capture shortly before downloading.
- **cURL parsing is for the bash variant.** "Copy as cURL (cmd)" and
  "Copy as PowerShell" output isn't understood.
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
- **Simple retry logic.** Network errors, 5xx and 429 responses are retried
  with exponential backoff. Other 4xx responses fail immediately. There's no
  `Retry-After` handling and no resuming of a partly finished download.

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
