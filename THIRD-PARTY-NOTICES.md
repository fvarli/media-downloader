# Third-party notices

Media Downloader is distributed under the MIT licence (see `LICENSE`). The standalone builds are
frozen with PyInstaller, so they contain a Python interpreter and the libraries listed below. Their
licences apply to those parts, and several of them require this notice to accompany the binary.

This file is shipped inside every standalone build.

## Bundled in the standalone builds

| Component | Licence |
| --- | --- |
| Python (CPython) | PSF-2.0 |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense |
| [yt-dlp-ejs](https://github.com/yt-dlp/ejs) | Unlicense AND MIT AND ISC |
| [Rich](https://github.com/Textualize/rich) | MIT |
| [certifi](https://github.com/certifi/python-certifi) | **MPL-2.0** |
| [requests](https://github.com/psf/requests) | Apache-2.0 |
| [urllib3](https://github.com/urllib3/urllib3) | MIT |
| [charset-normalizer](https://github.com/jawah/charset_normalizer) | MIT |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause |
| [websockets](https://github.com/python-websockets/websockets) | BSD-3-Clause |
| [pycryptodomex](https://github.com/Legrandin/pycryptodome) | BSD-2-Clause and Public Domain |
| [Brotli](https://github.com/google/brotli) | MIT |
| [Pygments](https://github.com/pygments/pygments) | BSD-2-Clause |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | MIT |
| [mdurl](https://github.com/executablebooks/mdurl) | MIT |
| [packaging](https://github.com/pypa/packaging) | Apache-2.0 OR BSD-2-Clause |
| [typing-extensions](https://github.com/python/typing_extensions) | PSF-2.0 |

certifi is MPL-2.0. Its source is unmodified and available from the project above; this notice and
the licence text accompany the binary as that licence requires.

## Deliberately not bundled

**mutagen is excluded.** yt-dlp's own PyInstaller hook lists it as a hidden import, so it used to be
compiled into the executable. It is GPL-2.0-or-later, and combining it with this MIT application
would place the whole binary under copyleft terms — for a feature that is never reachable here:
yt-dlp treats it as optional (`import mutagen` inside a `try/except`), only its thumbnail-embedding
postprocessor uses it, and this application never configures that. It is excluded in
`packaging/media-downloader.spec`, and a test asserts it stays excluded.

## Fetched at runtime, never bundled

FFmpeg and Deno are **not** part of any build. They are downloaded only when a user explicitly asks,
from a pinned URL over verified HTTPS, checked against a SHA-256 recorded in the source tree, and
installed inside this application's own data directory. See `README.md` for the full trust model.

| Tool | Licence | Source |
| --- | --- | --- |
| FFmpeg (Linux x86_64, Windows x64) | **LGPL-2.1-or-later** — an LGPL build, chosen so it can be redistributed | BtbN/FFmpeg-Builds |
| FFmpeg (macOS arm64) | **LGPL-2.1-or-later** — built from pinned sources by `packaging/ffmpeg/build-macos.sh` | this repository's `ffmpeg-n9.0.1-macos-arm64-1` release |
| Deno | MIT | denoland/deno |

The FFmpeg builds are configured **without** `--enable-gpl` and without `--enable-nonfree`. The
H.264 encoder is Cisco's `libopenh264` (BSD-2-Clause) rather than `libx264`, which is GPL. The
macOS build's exact configuration is recorded in its build script and can be read back from the
binary with `ffmpeg -buildconf`.

Because FFmpeg is dynamically fetched rather than linked into this application, and is used as a
separate executable, it remains a separate work under its own licence.
