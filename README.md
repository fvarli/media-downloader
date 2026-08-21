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
- Automatic service detection for the explicitly supported platforms.
- Best available video quality by default, with video and audio merged automatically.
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
| Linux | Python 3.10-3.13 | Manually verified |
| macOS | Python 3.10-3.13 | Not yet exercised |
| Windows | Python 3.10-3.13 | Not yet exercised |

The application uses only Python APIs and `pathlib` for path handling. It never shells out, never
assumes Bash or GNU utilities, and never hardcodes an OS-specific path.

> **Note on testing.** Two different things are verified, and they are not the same claim:
>
> - **Automated tests run on all three platforms.** The full suite, plus Ruff and strict mypy, runs
>   on Ubuntu, Windows and macOS against Python 3.10-3.13 via GitHub Actions on every push and pull
>   request. The code contains no platform-specific branches, and the suite covers Windows path and
>   filename rules explicitly.
> - **Real end-to-end downloads have only been performed on Linux** (Python 3.12.3). CI installs no
>   FFmpeg and contacts no media service, so actual downloading, stream merging and audio conversion
>   remain manually verified on Linux only.

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
| `-q`, `--quality` | `best`, `2160`, `1440`, `1080`, `720`, `480`, `360`, or `worst`. Default: `best`. Numeric values are an upper bound on height. |
| `--audio` | Download audio only. |
| `--audio-format` | `best`, `mp3`, `m4a`, `opus`, `flac`, `wav`. Default: `best`, which keeps the original stream without re-encoding. Any other value requires FFmpeg. |
| `--filename TEMPLATE` | yt-dlp output template for the file name, overriding the automatic naming below. Must be a bare file name — no directories. |
| `--info` | Print metadata and exit without downloading. |
| `--ffmpeg-location PATH` | Directory containing `ffmpeg` and `ffprobe`, if they are not on your PATH. |
| `--overwrite` | Overwrite an existing file instead of keeping it. |
| `-v`, `--verbose` | Show debug output, including yt-dlp's own. |
| `--quiet` | Print only the final path and errors. |
| `--version` | Print the version and exit. |
| `-h`, `--help` | Show the built-in help. |

`--verbose` and `--quiet` are mutually exclusive.

Filename templates use [yt-dlp's output template syntax](https://github.com/yt-dlp/yt-dlp#output-template);
useful fields include `%(title)s`, `%(id)s`, `%(ext)s`, `%(uploader)s` and `%(upload_date)s`.

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
├── .github/workflows/ci.yml      # Cross-platform test matrix
├── src/
│   └── media_downloader/
│       ├── __init__.py           # Version
│       ├── __main__.py           # python -m media_downloader
│       ├── cli.py                # Argument parsing, orchestration, exit codes
│       ├── config.py             # DownloadRequest; CLI > env > default precedence
│       ├── downloader.py         # yt-dlp wrapper (injectable factory)
│       ├── errors.py             # Exception hierarchy and ExitCode enum
│       ├── ffmpeg.py             # FFmpeg discovery and guidance
│       ├── jsruntime.py          # JavaScript runtime detection for yt-dlp
│       ├── logging_setup.py      # Logging configuration
│       ├── naming.py             # Output directory and template validation
│       ├── options.py            # Pure: request + tool status -> yt-dlp options
│       ├── progress.py           # Progress rendering
│       └── urls.py               # URL validation and service detection
└── tests/                        # Offline unit tests, one module per source module
```

The layering is one-directional:

```text
argv -> cli.py -> config.DownloadRequest -> options.build_ydl_opts -> downloader.Downloader
             |                                    |                          |
        urls / naming                 ffmpeg / jsruntime detection      yt_dlp.YoutubeDL
                                                                              |
                       cli prints the final path  <-- DownloadResult <--------+
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

## Possible future improvements

Not implemented, and not promised:

- Playlist and batch downloading
- A raw `--format` passthrough for power users
- Explicit, opt-in authentication for media the user is authorised to access
- Subtitle and thumbnail options
- A configuration file
- Packaged releases on PyPI

## Security and privacy

- **No shell execution.** yt-dlp is used as a Python library. No user input is ever passed to a
  shell, and `shell=True` appears nowhere in the codebase.
- **URL validation.** Only absolute `http` and `https` URLs with a hostname are accepted. `file://`,
  `javascript:`, `data:` and anything containing control characters are rejected before use.
- **No directory escape.** `--filename` accepts a bare file name only. Path separators, absolute
  paths, Windows drive letters, UNC prefixes and `..` are all rejected, so output cannot land
  outside `--output`.
- **No cookie or browser access.** Browser cookie extraction is not implemented and cannot be
  triggered accidentally. If authentication is added later it will be an explicit, documented,
  opt-in feature.
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
