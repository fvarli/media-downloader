# media-downloader

A cross-platform command-line tool for downloading publicly accessible media from a URL.

It is a deliberately thin, well-tested wrapper around [yt-dlp](https://github.com/yt-dlp/yt-dlp)
(extraction and downloading) and [FFmpeg](https://ffmpeg.org/) (merging, remuxing and audio
extraction). It does not reimplement site scraping, and it does not duplicate anything yt-dlp
already does reliably. What it adds is a predictable CLI, honest error messages, meaningful exit
codes, safe filename and path handling, and behaviour that is identical on Linux, macOS and Windows.

- **Source code:** <https://github.com/fvarli/media-downloader>
- **Issues and bug reports:** <https://github.com/fvarli/media-downloader/issues>

---

## Features

- Give it a URL, get a file. One required argument, sensible defaults for everything else.
- A local web interface (`--web`) for when a terminal is not the right tool.
- Automatic service detection for the explicitly supported platforms.
- Best available video quality by default, with video and audio merged automatically.
- **Universal playback mode** that normalises a video to H.264 + AAC in MP4 so it plays
  natively on iPhone, Mac, Windows and Android — converting only what is incompatible.
- Audio-only extraction, keeping the original stream when no conversion is asked for.
- Height-capped quality selection (`--quality 1080`).
- Metadata inspection without downloading (`--info`).
- Live progress bar in a terminal, plain throttled lines when output is redirected.
- The final file path is printed on stdout by itself, so it can be piped into other commands.
- Distinct exit codes for each failure mode, suitable for scripting.
- Graceful degradation when FFmpeg is missing, with a clear explanation instead of a stack trace.
- Filesystem-safe filenames on every platform, using the strictest (Windows) ruleset everywhere.

## Supported platforms

| OS | Automated tests (CI) | End-to-end download |
| --- | --- | --- |
| Linux | Python 3.10-3.13 | Manually verified, source and frozen artifact |
| macOS | Python 3.10-3.13 | Not yet exercised by a human |
| Windows | Python 3.10-3.13 | Not yet exercised by a human |

The application uses only Python APIs and `pathlib` for path handling. It never shells out, never
assumes Bash or GNU utilities, and never hardcodes an OS-specific path.

> **Note on testing.** Three different kinds of evidence exist here, and none of them stands in for
> another:
>
> - **Automated tests on all three platforms.** The full suite, plus Ruff and strict mypy, runs on
>   Ubuntu, Windows and macOS against Python 3.10-3.13 via GitHub Actions on every push and pull
>   request. The code contains no platform-specific branches, and the suite covers Windows path and
>   filename rules explicitly.
> - **Automated checks against the built artifact.** A separate workflow freezes the application on
>   each platform and exercises the packaged executable -- startup, loopback binding, the served
>   pages, the JSON API, the log written to the correct per-user directory, and a clean shutdown.
>   These checks are structural and offline. **CI success is not desktop verification:** no runner
>   double-clicks anything, sees a Gatekeeper prompt, or watches a video play.
> - **Manual verification by the project owner, on Linux only** (Python 3.12.3, x86_64). The frozen
>   Linux artifact has been run end to end: it starts, opens a browser, serves the interface,
>   downloads a real YouTube video that then plays, saves into the downloads directory, and exports
>   a support report. The web interface has also been used in a real browser for an Instagram
>   download.
> - **One playback result confirmed on real hardware.** A source that previously produced VP9 video
>   with HE-AAC audio was downloaded again in Universal mode, giving H.264 High + AAC-LC + yuv420p
>   in MP4. That exact file was transferred to an iPhone and played natively in Files / Quick Look.
>   This is a single confirmed case, not a claim about every Apple device or codec combination.
>
> **macOS and Windows have no manual verification at all.** They are covered by the first two kinds
> of evidence and nothing more. Real downloading, stream merging and audio conversion have been
> confirmed by a human only on Linux.

## Supported media services

Explicitly targeted, detected by name, and covered by the test suite:

- YouTube (including `youtu.be` and `music.youtube.com`)
- Instagram
- TikTok (including `vm.tiktok.com` and `vt.tiktok.com` short links)
- X / Twitter (both `x.com` and `twitter.com`)

Any other public `http(s)` URL is still attempted through yt-dlp, which supports well over a
thousand sites. When the host is not one of the four above, the tool prints a short notice and
continues on a best-effort basis.

---

## Requirements

### Python

**Python 3.10 or newer.**

This matches yt-dlp's own minimum. Check what you have:

```bash
python3 --version    # Linux / macOS
```

```powershell
py --version         # Windows
```

### FFmpeg

FFmpeg is a **separate program**, not a Python package, and is not installed by `pip`. This project
never installs it for you.

It is needed for:

- **merging** separate video and audio streams — the highest-quality formats on YouTube and similar
  sites are served as two separate streams
- **remuxing** into a suitable container
- **audio extraction** from a video file
- **format conversion**, for example `--audio-format mp3`

**If FFmpeg is missing, the tool does not crash.** It prints a plain-language explanation and then:

- for a normal video download, falls back to pre-merged (progressive) formats — the download
  succeeds, but the available quality may be lower;
- for `--audio` with no explicit format, saves the original audio stream as-is;
- for an explicit conversion such as `--audio-format mp3`, stops with exit code `4` and explains
  what is missing.

Typical installation:

```bash
sudo apt install ffmpeg          # Debian / Ubuntu
sudo dnf install ffmpeg          # Fedora
sudo pacman -S ffmpeg            # Arch
brew install ffmpeg              # macOS (Homebrew)
```

```powershell
winget install Gyan.FFmpeg       # Windows (winget)
choco install ffmpeg             # Windows (Chocolatey)
scoop install ffmpeg             # Windows (Scoop)
```

Or download a build from <https://ffmpeg.org/download.html> and point the tool at it with
`--ffmpeg-location`.

Verify:

```bash
ffmpeg -version
ffprobe -version
```

Both are required — yt-dlp uses `ffprobe` to inspect streams.

### JavaScript runtime (optional, YouTube)

yt-dlp solves YouTube's JavaScript challenges using `yt-dlp-ejs`, which is installed automatically
as part of this project's dependencies. `yt-dlp-ejs` provides the JavaScript but still needs a
runtime to execute it, and **yt-dlp only enables Deno by default**.

Without a runtime, some YouTube downloads may fail or be limited to lower-quality formats. You have
two options:

```bash
# Option 1: install the pip-packaged Deno runtime into the project environment
python -m pip install -e ".[js]"
```

```bash
# Option 2: install Deno, Node.js or Bun system-wide, so it is on your PATH
```

This tool detects Deno, Node and Bun, and automatically enables whichever it finds — so a
system-wide Node.js works without any extra flags. If none is found and you are downloading from
YouTube, it prints a one-line notice. All challenge handling stays inside yt-dlp; this project
implements none of its own.

---

## Installation

### 1. Get the code

```bash
git clone https://github.com/fvarli/media-downloader.git
cd media-downloader
```

Or simply `cd` into the project directory if you already have it.

### 2. Create the environment and install

The project uses Python's built-in `venv` module. The environment lives in `.venv/` inside the
project and is excluded from Git. Pick the block for your shell and run it top to bottom — each one
is complete on its own.

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

**Windows PowerShell**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

If PowerShell blocks the activation script, allow it for the current session only:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

**Windows Command Prompt**

```cmd
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e .
```

To leave the environment, run `deactivate` in any of the three shells.

### 3. Optional extras

Replace `python -m pip install -e .` above with one of these to pull in more:

```bash
python -m pip install -e ".[dev]"    # tests, linter, type checker
python -m pip install -e ".[js]"     # bundled Deno runtime for YouTube
python -m pip install -e ".[dev,js]" # both
```

### Working without activating

Activation is only a convenience. The virtual environment's interpreter can always be invoked
directly, which is what scripts, CI and editors should do:

```bash
.venv/bin/python -m pip install -e ".[dev]"          # Linux / macOS
```

On Windows the same interpreter lives at `.venv\Scripts\python.exe`, and the command is identical
in **both** PowerShell and Command Prompt:

```text
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### Keeping yt-dlp current

Sites change their internals frequently. When a previously working URL starts failing, updating the
extractor is almost always the fix:

```bash
python -m pip install -U yt-dlp
```

The project deliberately sets no upper version bound on yt-dlp, so this always works.

---

## Usage

```bash
media-downloader "URL"
```

The URL should be quoted, since it usually contains characters your shell would otherwise interpret.

Running straight from a source checkout, without installing:

```bash
python main.py "URL"
```

Or as a module, once installed:

```bash
python -m media_downloader "URL"
```

All three forms are equivalent — `main.py` is a small shim around the same entry point.

### Web interface

If you would rather not use a terminal:

```bash
media-downloader --web
```

That starts a small local server, prints its address and opens your browser:

```text
Media Downloader is running at http://127.0.0.1:8765
Saving downloads to /home/you/Downloads/Media Downloader
Press Ctrl+C to stop.
```

Paste a link, pick Video or Audio, choose a quality and press Download. Progress, the finished file
name and any error appear on the page, and the download folder can be opened from the footer.

A few things worth knowing:

- **It is not a website.** The server listens on `127.0.0.1` only, so nothing outside your computer
  can reach it. There are no accounts and no database, and nothing is stored between runs -- the
  download list covers the current session only.
- **It saves to `~/Downloads/Media Downloader`**, not to `./downloads` like the CLI. A double-clicked
  application has no meaningful working directory, so the web interface uses a predictable place.
- **One download at a time.** Starting a second while one is running is refused with a clear message.
- If no browser can be opened -- over SSH, in WSL, on a headless machine -- open the printed URL
  yourself.
- **There is no Docker and no container.** The page is served by the same Python installation that
  runs the CLI, so if you can run `media-downloader`, you can run this -- there is nothing else to
  install or configure.
- **It can install FFmpeg and Deno for you, but only if you ask.** See
  [Optional tools](#optional-tools) below.
- It is built on Python's standard-library HTTP server, which adds no dependencies and starts
  instantly. The standard library notes that this server is not intended for internet-facing
  production use; that is why it is bound to loopback and cannot be configured to listen elsewhere.

### Examples

```bash
# Download the best available quality into ./downloads
media-downloader "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

# Audio only, keeping the original stream (no re-encoding)
media-downloader "URL" --audio

# Audio converted to MP3 (requires FFmpeg)
media-downloader "URL" --audio --audio-format mp3

# Explicitly ask for the best quality (this is the default)
media-downloader "URL" --quality best

# Cap the resolution at 1080p
media-downloader "URL" --quality 1080

# Choose the download directory
media-downloader "URL" --output ~/Downloads

# Choose the file name using a yt-dlp output template
media-downloader "URL" --filename "%(title)s.%(ext)s"

# Inspect the media without downloading it
media-downloader "URL" --info

# Capture the resulting path in a shell variable
FILE=$(media-downloader "URL" --quiet)
```

Windows uses different path and environment-variable syntax, and the two Windows shells differ from
each other as well — PowerShell's `$env:VAR` form is **not** valid in Command Prompt.

**Windows PowerShell**

```powershell
media-downloader "URL" --output "$env:USERPROFILE\Downloads"
$file = media-downloader "URL" --quiet
```

**Windows Command Prompt**

```cmd
media-downloader "URL" --output "%USERPROFILE%\Downloads"
for /f "delims=" %f in ('media-downloader "URL" --quiet') do set FILE=%f
```

Everything else — every flag, the exit codes, the output template syntax — is identical on all three
operating systems.

### CLI options

| Option | Description |
| --- | --- |
| `URL` | Required. The public `http(s)` URL of the media. |
| `-o`, `--output DIR` | Directory to save into. Default: `./downloads`. |
| `-q`, `--quality` | `best`, `2160`, `1440`, `1080`, `720`, `480`, `360`, or `worst`. Default: `best`. Numeric values are an upper bound on height; if nothing exists below the bound, the lowest available is downloaded and you are told. |
| `--compatibility` | `universal` or `original`. Default: `original` on the command line, `universal` in the web interface. See [Playback compatibility](#playback-compatibility). |
| `--audio` | Download audio only. |
| `--audio-format` | `best`, `mp3`, `m4a`, `opus`, `flac`, `wav`. Default: `best`, which keeps the original stream without re-encoding. Any other value requires FFmpeg. |
| `--filename TEMPLATE` | yt-dlp output template for the file name, overriding the automatic naming below. Must be a bare file name — no directories. |
| `--info` | Print metadata and exit without downloading. |
| `--ffmpeg-location PATH` | Directory containing `ffmpeg` and `ffprobe`, if they are not on your PATH. |
| `--overwrite` | Overwrite an existing file instead of keeping it. |
| `-v`, `--verbose` | Show debug output, including yt-dlp's own. |
| `--quiet` | Print only the final path and errors. |
| `--version` | Print the version and exit. |
| `--web` | Start the local web interface instead of downloading. Cannot be combined with a URL. |
| `-h`, `--help` | Show the built-in help. |

`--verbose` and `--quiet` are mutually exclusive.

Filename templates use [yt-dlp's output template syntax](https://github.com/yt-dlp/yt-dlp#output-template);
useful fields include `%(title)s`, `%(id)s`, `%(ext)s`, `%(uploader)s` and `%(upload_date)s`.

### Playback compatibility

`.mp4` names a container, not the codecs inside it. A real download from this project produced an
MP4 holding **VP9 video and HE-AAC audio**: it played on Linux, and on an iPhone it would not play
normally, because Apple's native players decode H.264 and AAC-LC. That regression is what this
feature exists to prevent.

**Universal** — `--compatibility universal`, and the default in the web interface.

The finished file is MP4 with H.264 video, AAC-LC audio, `yuv420p` and faststart. Broad native
playback on iPhone, iPad, macOS, Windows, Android and mainstream browsers; not a guarantee about
every device ever built.

Quality is never traded for convenience:

- Resolution and frame rate are chosen first. A compatible H.264 stream is preferred only when it
  costs nothing, so 4K is never silently downgraded to 1080p to avoid a conversion.
- Nothing is re-encoded that does not need to be. A file that is already H.264 + AAC-LC is
  remuxed, not encoded again — no generation loss, no wasted CPU.
- When only the audio is unsuitable, the video is copied and the audio alone is converted.
- The finished file is then inspected with `ffprobe`, because FFmpeg exiting successfully is not
  evidence that a phone will play the result.

Conversion happens after the download and can take longer than the download itself, especially at
high resolutions. It requires FFmpeg: without `ffprobe` there is no way to verify what was
produced, so the mode refuses to run rather than claim a compatibility it cannot check.

Which H.264 encoder is used depends on the FFmpeg you have. `libx264` is preferred, but it is
GPL-licensed and therefore absent from LGPL builds — including the one this project installs for
you — so those use Cisco's BSD-licensed `libopenh264` instead. Both produce H.264 that plays
natively; `libx264` reaches a given quality in fewer bits.

**Original** — `--compatibility original`, the command-line default.

Today's behaviour, unchanged: the source codecs are kept, which is what you want when archiving at
maximum quality. The result may be VP9, AV1 or another modern codec, and **native playback is not
guaranteed** — particularly in Apple and Windows players.

### When the requested quality or format is unavailable

Format availability differs by site and by video, so selection is a chain of candidates rather than
one demand: video plus audio, then a single file already containing both, then video alone. The
first that matches wins.

Two of those outcomes are worth stating plainly, and the application says so rather than leaving
them to be discovered:

- **A video with no usable audio still downloads**, and the result says it has no sound. Refusing a
  perfectly good video stream because no audio accompanies it would be worse.
- **A quality cap is an upper bound.** Asking for 1080p where only 720p exists gives 720p. Where
  nothing at all exists below the cap, the lowest available is downloaded and the result says the
  cap could not be honoured — somebody asking for 360p wants a small file, not an error.

When genuinely nothing matches, the error says so: *"No downloadable format matched the selected
quality/options."* That is a statement about the request, not about the media, and it is kept
separate from the case where a video really is private, removed, age-restricted, region-locked or
DRM-protected.

### Automatic file names

Without `--filename`, names are generated as `<title> - <id>.<ext>`:

```text
Big Buck Bunny - aqz-KE-bpKQ.mp4
Trend - 2090546322570924033.mp4
```

Social platforms often put links and emoji in the title, so the title is cleaned first: URLs, emoji
and characters that no filesystem should carry are removed, and the leftover separator and
whitespace debris is tidied up. Text is never transliterated, so Turkish and other non-Latin scripts
stay readable. The media ID is always appended, which keeps names unique and traceable back to their
source; if cleaning leaves no usable title, the uploader or service name is used instead.

### If something goes wrong

The application keeps a small diagnostic log and can produce a report you can send on:

1. Open Media Downloader.
2. Open **Help & diagnostics**.
3. Click **Export support report**.
4. Send the generated `.txt` file to whoever maintains this.

The report is written to your downloads folder and **is never uploaded anywhere** -- there is no
telemetry and nothing is sent automatically. It contains version and platform details, where things
are installed, the most recent error and a short excerpt of the log. Session tokens, cookies,
credentials and request headers are excluded, and URLs are stripped of their query strings before
they reach it.

If an unexpected error occurs, the interface shows a short code such as `MD-20260823-A1B2C3`. Quoting
that code lets the matching log entry be found.

Logs are bounded in size and live alongside the application's other data:

| Platform | Log folder |
| --- | --- |
| Linux | `~/.local/share/media-downloader/logs/` (or `$XDG_DATA_HOME`) |
| macOS | `~/Library/Application Support/Media Downloader/logs/` |
| Windows | `%LOCALAPPDATA%\Media Downloader\logs\` |

### Optional tools

FFmpeg and a JavaScript runtime both make downloads better, and neither can be installed with `pip`.
If one is missing, the web interface can fetch it for you -- and only then:

- **nothing is downloaded unless you click the button.** There is no prompt at startup, and
  declining is always safe: everything that does not need that tool keeps working exactly as before.
- the download comes from a **fixed, pinned URL over HTTPS** recorded in the source tree; the browser
  cannot name a URL, a version or a location.
- the file's **SHA-256 is verified before anything is unpacked**, and nothing is ever executed before
  it verifies. A failed download or a mismatched checksum is deleted, leaving nothing behind.
- it is stored in this application's own directory. **`PATH` is never modified, nothing is installed
  system-wide, and no step needs administrator rights.**
- a tool you already have installed is always preferred over a downloaded copy.

| OS | Where they are kept |
| --- | --- |
| Linux | `~/.local/share/media-downloader/tools/` (or `$XDG_DATA_HOME`) |
| macOS | `~/Library/Application Support/Media Downloader/tools/` |
| Windows | `%LOCALAPPDATA%\Media Downloader\tools\` |

Deleting that folder undoes the installation completely.

Which tools can be installed automatically depends on the platform, because each one needs a source
we can pin and verify:

| Platform | FFmpeg | JavaScript runtime (Deno) |
| --- | --- | --- |
| Linux x86_64 | Yes (LGPL build) | Yes |
| Windows x64 | Yes (LGPL build) | Yes |
| macOS arm64 | Yes (built by this project) | Yes |
| macOS Intel | **No** -- see below | Yes |

**macOS FFmpeg is built here rather than taken from a third party.** Every macOS provider examined
failed at least one requirement: the only one linked from ffmpeg.org publishes no SHA-256 checksum,
and the alternative that does publish checksums serves them behind a URL replaced in place when it
rebuilds, without publishing its build flags -- so neither a stable pin nor the licence could be
established. So the Apple Silicon build is made from pinned sources by
`packaging/ffmpeg/build-macos.sh`, published under its own tag, and pinned by checksum like every
other tool. **Intel Macs still have no verified source** and are reported as unsupported; install
FFmpeg with Homebrew there. A system copy is always preferred anyway.

The CLI never downloads a tool. It uses one if it finds it, and otherwise behaves exactly as it
always has.

Passing `--filename` turns this off completely — your template is used exactly as written, subject
only to the safety checks that keep output inside `--output`.

### Environment variables

| Variable | Effect |
| --- | --- |
| `MEDIA_DOWNLOADER_OUTPUT` | Default download directory. |
| `MEDIA_DOWNLOADER_FFMPEG` | Directory containing `ffmpeg` and `ffprobe`. |

Precedence is **command-line flag > environment variable > built-in default**.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success |
| `1` | Unexpected internal error |
| `2` | Command-line usage error |
| `3` | Invalid or unsupported URL |
| `4` | FFmpeg required but unavailable |
| `5` | Download or extraction failed (network, extractor) |
| `6` | Media not publicly accessible (private, removed, age-restricted, geo-blocked, DRM) |
| `7` | Output directory or filename problem |
| `130` | Interrupted with Ctrl-C |

---

## Development

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

**Windows PowerShell**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

**Windows Command Prompt**

```cmd
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

### Running tests

```bash
python -m pytest
```

With a coverage report:

```bash
python -m pytest --cov=media_downloader --cov-report=term-missing
```

The suite is fully offline: no test makes a network request. `yt_dlp.YoutubeDL` is replaced with a
fake through the `Downloader` class's injectable factory, so download logic, error translation and
final-path resolution are all covered without touching the internet.

### Lint and type checks

```bash
python -m ruff check .        # lint
python -m ruff format .       # format
python -m ruff format --check .
python -m mypy src            # strict type checking
```

All three are configured in `pyproject.toml` and currently pass cleanly.

Without activating the environment, prefix each command with the interpreter path — for example
`.venv/bin/python -m pytest` on Linux and macOS, or `.venv\Scripts\python.exe -m pytest` on Windows.

### Building a standalone artifact (in development)

Work has started on standalone builds that need no Python installed. **Nothing is released yet.**
There is no v0.3.0, and the artifacts produced by CI are validation builds for development, **not
official downloads**.

```bash
python -m pip install -e ".[dev,packaging]"
python -m PyInstaller packaging/media-downloader.spec --noconfirm
./dist/media-downloader/media-downloader --web
```

The result is a self-contained directory in `dist/media-downloader/` holding the executable and its
`_internal/` support files. It needs neither the virtual environment nor a system Python.

Builds are produced with **Python 3.12**; the source test matrix continues to cover 3.10-3.13.

Console or windowed is an explicit build-time choice. `MD_WINDOWED_BUILD=1` produces the shape a
user would actually double-click -- no terminal behind the window, and on macOS a `.app` bundle --
which is what the macOS and Windows validation jobs now build. Linux stays a console build, where
there is no such distinction to make and printed output is still worth checking.

```bash
MD_WINDOWED_BUILD=1 python -m PyInstaller packaging/media-downloader.spec --noconfirm
```

Because a windowed build has no console, the artifact smoke test takes every observation from the
file log and the HTTP API instead of standard output. Each server it starts gets its own app-data
directory and reads its address out of that directory's own log, so it cannot accidentally test a
leftover instance.

FFmpeg and Deno are deliberately **not** bundled -- see [Optional tools](#optional-tools).

Not yet done, and not promised: code signing, notarization, installers, and published binaries.

### macOS FFmpeg (built here)

No published macOS provider meets the licensing and provenance requirements at once, so
`packaging/ffmpeg/build-macos.sh` and the manually-dispatched `macos-ffmpeg` workflow build one
from pinned sources: FFmpeg and libopenh264 pinned by commit, libopus and LAME by SHA-256. The
build refuses to package a binary whose own `-buildconf` shows `--enable-gpl` or
`--enable-nonfree`, or one that links anything outside the system libraries, and it encode-tests
every capability -- MP3, Opus, AAC, FLAC, WAV, H.264 and a merge -- before packaging anything.

libopenh264 is there because Universal mode needs an H.264 encoder and `libx264` is GPL, which an
LGPL build cannot contain. Cisco's encoder is BSD-2-Clause and sits outside FFmpeg's GPL-only
library list, and it is the same encoder the Linux and Windows builds already use.

The result is published under its own tag as a **managed-tool dependency, not an application
release**, which is what gives the manifest a durable public URL to pin.

### Continuous integration

`.github/workflows/ci.yml` runs the same four checks on every push to `main` and every pull request
targeting it, across a matrix of Ubuntu, Windows and macOS against Python 3.10, 3.11, 3.12 and 3.13.

CI installs no FFmpeg, Node or Deno and contacts no media service: the unit suite is entirely
offline, so the workflow is deterministic.

## Project structure

```text
media-downloader/
├── main.py                       # Shim: python main.py "URL"
├── pyproject.toml                # Packaging, dependencies, ruff/mypy/pytest config
├── LICENSE                       # MIT
├── README.md
├── .gitignore
├── .gitattributes                # LF line endings across all platforms
├── .github/workflows/            # Source CI matrix, standalone builds, macOS FFmpeg
├── packaging/                    # PyInstaller spec, artifact metadata, smoke tests
│   └── ffmpeg/                   # macOS LGPL FFmpeg build and archive verification
├── src/
│   └── media_downloader/
│       ├── __init__.py           # Version
│       ├── __main__.py           # python -m media_downloader
│       ├── cli.py                # Argument parsing, orchestration, exit codes
│       ├── diagnostics.py        # Bounded logging, error IDs, support report
│       ├── paths.py              # Per-user app data, tools and log directories
│       ├── config.py             # DownloadRequest; CLI > env > default precedence
│       ├── downloader.py         # yt-dlp wrapper (injectable factory)
│       ├── errors.py             # Exception hierarchy and ExitCode enum
│       ├── ffmpeg.py             # FFmpeg discovery and guidance
│       ├── jsruntime.py          # JavaScript runtime detection for yt-dlp
│       ├── logging_setup.py      # Logging configuration
│       ├── naming.py             # Output directory and template validation
│       ├── options.py            # Pure: request + tool status -> yt-dlp options
│       ├── progress.py           # Progress rendering
│       ├── service.py            # Shared application layer (CLI + web)
│       ├── tools/                # Optional managed tools (FFmpeg, Deno)
│       │   ├── manifest.py       # Pinned, checksum-verified sources
│       │   ├── verify.py         # Streamed SHA-256, fail-closed
│       │   ├── archive.py        # Traversal-proof extraction
│       │   └── manager.py        # Install and discovery
│       ├── urls.py               # URL validation and service detection
│       └── web/                  # Local web interface
│           ├── api.py            # Endpoint handlers
│           ├── jobs.py           # Download jobs and progress state
│           ├── launcher.py       # Start server, open browser
│           ├── security.py       # Localhost request guards
│           ├── server.py         # HTTP server and routing
│           ├── system.py         # Folders, browser, native dialogs
│           ├── tools.py          # Consent-gated tool installation
│           └── static/           # index.html, app.css, app.js
└── tests/                        # Offline unit tests, one module per source module
```

The layering is one-directional:

```text
  CLI  --.
         |
         +--> service.py --> downloader.Downloader --> yt_dlp.YoutubeDL --> FFmpeg
         |    (shared)              ^
 Web UI -'                          |
                       config / urls / naming / options

Both front ends build the same DownloadRequest and use the same Downloader.
There is exactly one download implementation: the web layer never invokes the
CLI and never imports yt-dlp itself.
```

`urls`, `naming`, `config`, `options`, `ffmpeg` and `jsruntime` are pure or nearly so, which is
where the bulk of the test suite lives. `downloader` is a thin, injectable adapter.

---

## Troubleshooting

**`ffmpeg not found` warnings, or downloads capped at a lower quality than expected**
Install FFmpeg (see [Requirements](#ffmpeg)), or point at an existing installation with
`--ffmpeg-location /path/to/ffmpeg/bin`. Both `ffmpeg` and `ffprobe` must be present.

**A YouTube download fails, or only low-quality formats are offered**
Install a JavaScript runtime — `pip install -e ".[js]"`, or a system-wide Deno, Node.js or Bun. See
[JavaScript runtime](#javascript-runtime-optional-youtube).

**A URL that used to work now fails with exit code 5**
The site probably changed. Update the extractor: `python -m pip install -U yt-dlp`.

**Exit code 6, "not publicly accessible"**
The media is private, removed, age-restricted, region-locked or DRM-protected. This tool only
downloads publicly accessible media and will not attempt to work around access controls.

**`media-downloader: command not found`**
The virtual environment is not active. Either activate it, or call the interpreter directly:
`.venv/bin/python main.py "URL"` (`.venv\Scripts\python.exe main.py "URL"` on Windows).

**PowerShell refuses to run `Activate.ps1`**
Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process`, which applies to the current
session only.

**The URL is mangled, or exit code 3 on a URL that looks fine**
Quote it. Unquoted URLs containing `&`, `?` or `%` are altered by the shell before the program sees
them.

**Long filenames fail on Windows**
File names are trimmed to 200 characters, but a deeply nested `--output` directory can still exceed
Windows' path limit. Use a shorter output path, or enable long-path support in Windows.

---

## Current limitations

These are accurate as of this version. They are limitations, not planned features.

- **Single media item per invocation.** Playlists, channels and profile URLs are not expanded; if a
  URL resolves to a collection, only the first item is downloaded.
- **No batch input.** One URL per run; no file-of-URLs mode.
- **The web interface runs one download at a time**, with no queue, cancel or retry.
- **The web interface keeps no history between runs.** Its session list is in memory only.
- **Standalone builds are development-only.** They can be built and are exercised by CI, but no
  standalone release exists; signing, notarization and installers remain future work.
- **Managed FFmpeg is unavailable on Intel Macs.** Apple Silicon is supported: the build is made
  by this project from pinned sources — see `packaging/ffmpeg/build-macos.sh` — and published
  under its own tag so the manifest has a durable URL to pin. No third-party macOS provider met
  the bar, so Intel Macs still have no entry and are reported as unsupported rather than given a
  hash nobody verified. Install FFmpeg with Homebrew there instead.
- **No authentication.** No cookies, no browser-cookie extraction, no logins. Only publicly
  accessible media can be downloaded.
- **No subtitle, thumbnail or metadata embedding.**
- **No raw yt-dlp format passthrough.** Format selection is limited to the `--quality` choices.
- **No configuration file.** Configuration is limited to CLI flags and the two environment variables.
- **No download archive or resume bookkeeping across runs.** yt-dlp's own partial-file resume works
  within a run, but nothing is recorded between runs.
- **Progress is per-stream.** When video and audio are downloaded separately, two progress phases
  are shown rather than a single combined total.
- **Real downloads verified on Linux only.** The automated suite passes on Windows and macOS in CI,
  but CI runs no FFmpeg and downloads no media, so end-to-end downloading has not been exercised on
  Windows or macOS hardware. See [Supported platforms](#supported-platforms).
- **The windowed macOS and Windows builds have never been opened by a human.** CI builds and
  exercises them, but nobody has double-clicked one, dismissed a Gatekeeper or SmartScreen prompt,
  or confirmed that no console window appears.

## Possible future improvements

Not implemented, and not promised:

- Playlist and batch downloading
- A download queue with cancel and retry in the web interface
- Standalone builds for macOS, Windows and Linux
- A raw `--format` passthrough for power users
- Explicit, opt-in authentication for media the user is authorised to access
- Subtitle and thumbnail options
- A configuration file
- Packaged releases on PyPI

## Security and privacy

- **No shell execution.** yt-dlp is used as a Python library, and `shell=True` appears nowhere in
  the codebase. The one place this project starts another program is "Open folder", which passes the
  download directory to `xdg-open`, `open` or `os.startfile` as a single argument -- never a command
  line, and never a path that came from the browser.
- **URL validation.** Only absolute `http` and `https` URLs with a hostname are accepted. `file://`,
  `javascript:`, `data:` and anything containing control characters are rejected before use.
- **No directory escape.** `--filename` accepts a bare file name only. Path separators, absolute
  paths, Windows drive letters, UNC prefixes and `..` are all rejected, so output cannot land
  outside `--output`.
- **No cookie or browser access.** Browser cookie extraction is not implemented and cannot be
  triggered accidentally. If authentication is added later it will be an explicit, documented,
  opt-in feature.
- **The web interface is loopback-only.** It binds `127.0.0.1`, sends no CORS headers, requires a
  per-session token generated at startup, rejects requests whose `Host` or `Origin` is not its own,
  and requires JSON for anything that changes state. Together these stop other websites and
  DNS-rebinding tricks from driving it.
- **The browser cannot choose paths.** The download directory is fixed by the server and no endpoint
  accepts a filesystem path. "Open folder" takes no argument and can open only that one directory.
- **No telemetry.** Nothing is collected, and nothing is sent anywhere except to the media host, by
  yt-dlp, in order to fetch what you asked for.
- **No credentials on disk.** The project stores no tokens or secrets. `.gitignore` already excludes
  cookie and credential file patterns, so such files cannot be committed if that support is ever
  added.

## Legal and responsible use

This tool downloads **publicly accessible** media only.

- It does not bypass DRM, paywalls, authentication or any other access control, and it never will.
- You are responsible for complying with the terms of service of the sites you use and with the
  copyright law of your jurisdiction.
- Downloading content you do not have the right to copy may be unlawful where you live.
- Please respect creators: use this for personal, archival, educational or otherwise permitted
  purposes.

## Contributing

Contributions are welcome.

1. Open an issue describing the change before starting substantial work.
2. Keep the existing structure: CLI, configuration, download logic, validation and utilities stay in
   separate modules.
3. Do not reimplement anything yt-dlp already does — wrap it.
4. Add type hints to all new code.
5. Add tests. New logic must be testable offline; no test may make a network request.
6. Before opening a pull request:

   ```bash
   python -m pytest
   python -m ruff check .
   python -m ruff format --check .
   python -m mypy src
   ```

7. Keep the README truthful. Document what exists, not what is planned.

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Ferzender Varli.

This project depends on, but does not include, yt-dlp (Unlicense) and FFmpeg (LGPL or GPL depending
on the build). Those are separate works under their own licenses.
