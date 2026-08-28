"""Which FFmpeg the compatibility normaliser actually uses.

A real Windows machine installed the managed FFmpeg through the interface,
downloaded a video, and then failed on the very next step:

    Postprocessing: ffprobe not found. Please install or provide the path
    using --ffmpeg-location

while ``ffprobe.exe`` sat in the managed directory the application had just
installed, and which it was already handing to yt-dlp as ``ffmpeg_location``.

yt-dlp resolves both binaries once, in ``FFmpegPostProcessor.__init__``, and
never recomputes them -- there is no ``set_downloader`` override. So a
postprocessor constructed before it has a downloader cannot see
``ffmpeg_location`` at all, and falls back to the bare names ``ffmpeg`` and
``ffprobe``: whatever PATH offers, and nothing whatsoever when PATH offers
nothing. That is why this survived every green CI run on Linux and macOS and
appeared only on a Windows machine with no system FFmpeg.

Two layers here, because neither alone covers the ground:

* The resolution tests assert where the normaliser *would* look. They need no
  binaries, so they run on every platform in source CI -- including the Windows
  job, which is what stops Linux masking this again.
* The probe test actually runs ffprobe out of a directory that is not on PATH,
  which is the call that failed. It needs a real FFmpeg, and source CI
  deliberately installs none, so it runs locally and wherever one exists.

The end-to-end proof on a frozen build with a genuinely managed FFmpeg is the
standalone-build workflow, not this file.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from yt_dlp import YoutubeDL

from media_downloader.config import CompatibilityMode, DownloadRequest
from media_downloader.ffmpeg import FFmpegStatus
from media_downloader.normalize import register_universal_compatibility
from media_downloader.options import build_ydl_opts

#: Resolved while PATH is still intact, because the tests below empty it.
SYSTEM_FFMPEG = shutil.which("ffmpeg")
SYSTEM_FFPROBE = shutil.which("ffprobe")

ffmpeg_required = pytest.mark.skipif(
    not (SYSTEM_FFMPEG and SYSTEM_FFPROBE),
    reason="needs a real FFmpeg to probe with",
)


def registered(ydl: YoutubeDL) -> Any:
    """The normaliser, taken from where yt-dlp actually keeps it.

    Reaching into ``_pps`` rather than keeping the object we built is
    deliberate: it proves the processor that will run is the one under test,
    not a copy that happened to be configured correctly.
    """
    register_universal_compatibility(ydl)
    return ydl._pps["post_process"][-1]


def resolved(pp: Any, program: str) -> Path:
    """Where ``pp`` would look for ``program``.

    This reads ``_paths``, which is precisely what ``_determine_executables``
    returns and precisely what was wrong. The public ``executable`` property
    cannot be used here: it resolves to None until the binary has been run and
    has reported a version, so it answers "does this machine have FFmpeg"
    rather than "which FFmpeg was configured" -- and the second question is the
    one that had the wrong answer.
    """
    return Path(pp._paths[program])


def install_shaped_like(directory: Path) -> Path:
    """A directory shaped like an FFmpeg installation, holding no real binaries.

    yt-dlp resolves a directory location by joining the program names onto it,
    so nothing here has to be executable -- which is what keeps this test
    meaningful on Windows, where an executable cannot simply be written out.
    Note the absence of ``.exe``: yt-dlp joins the bare name, and Windows
    appends the extension itself when it launches the process.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ffmpeg").write_text("")
    (directory / "ffprobe").write_text("")
    return directory


# -- where it looks --------------------------------------------------------


def test_the_normaliser_uses_the_configured_ffmpeg(tmp_path: Path) -> None:
    """The whole failure in one assertion.

    Both of these used to come back as the bare names, so on a machine whose
    PATH has no FFmpeg -- exactly the machine that had just installed a managed
    one -- the first probe failed and took the download with it.
    """
    location = install_shaped_like(tmp_path / "managed")
    pp = registered(YoutubeDL({"quiet": True, "ffmpeg_location": str(location)}))

    assert resolved(pp, "ffmpeg") == location / "ffmpeg"
    assert resolved(pp, "ffprobe") == location / "ffprobe"


def test_the_configured_location_beats_whatever_is_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second fault with the same cause, and not a Windows one.

    An unbound processor asks PATH for ``ffmpeg`` and gets an answer on any
    ordinary Linux or macOS machine -- so an explicit ``--ffmpeg-location`` was
    quietly discarded there too and the wrong binary did the work. It became
    visible only on Windows, where the wrong answer was no answer.
    """
    on_path = install_shaped_like(tmp_path / "on-path")
    configured = install_shaped_like(tmp_path / "configured")
    monkeypatch.setenv("PATH", str(on_path))

    pp = registered(YoutubeDL({"quiet": True, "ffmpeg_location": str(configured)}))

    assert resolved(pp, "ffprobe") == configured / "ffprobe"


def test_nothing_on_path_at_all_still_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's machine: a managed install and a PATH with no FFmpeg."""
    location = install_shaped_like(tmp_path / "managed")
    monkeypatch.setenv("PATH", "")

    pp = registered(YoutubeDL({"quiet": True, "ffmpeg_location": str(location)}))

    assert resolved(pp, "ffprobe") == location / "ffprobe"


@pytest.mark.parametrize("source", ["system", "explicit", "managed"])
def test_each_way_of_supplying_ffmpeg_reaches_the_normaliser(tmp_path: Path, source: str) -> None:
    """Discovery has three routes and all three must arrive at the same place.

    They differ only in how ``detect_ffmpeg`` finds the directory -- covered in
    test_ffmpeg.py -- so this pins the join from there to the normaliser rather
    than repeating discovery.
    """
    location = install_shaped_like(tmp_path / source)
    status = FFmpegStatus(ffmpeg=location / "ffmpeg", ffprobe=location / "ffprobe")
    request = DownloadRequest(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        output_dir=tmp_path / "out",
        compatibility=CompatibilityMode.UNIVERSAL,
    )

    opts = build_ydl_opts(request, status)
    assert opts["ffmpeg_location"] == str(location)

    pp = registered(YoutubeDL({**opts, "quiet": True}))
    assert resolved(pp, "ffprobe") == location / "ffprobe"


# -- actually running it ---------------------------------------------------


@ffmpeg_required
def test_the_normaliser_probes_a_real_file_from_a_directory_off_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs the probe rather than inspecting where it would have looked.

    A copied FFmpeg in a directory that is not on PATH is a managed
    installation in miniature, and this is the exact call that failed on the
    owner's machine. Unbound, it raises PostProcessingError("ffprobe not
    found ..."); the file names keep the platform's own convention so Windows
    still finds them when it appends ``.exe``.
    """
    assert SYSTEM_FFMPEG and SYSTEM_FFPROBE
    media = tmp_path / "clip.mp4"
    subprocess.run(
        [
            SYSTEM_FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )  # fmt: skip

    location = tmp_path / "managed"
    location.mkdir()
    for binary in (SYSTEM_FFMPEG, SYSTEM_FFPROBE):
        shutil.copy2(binary, location / Path(binary).name)

    monkeypatch.setenv("PATH", "")
    pp = registered(YoutubeDL({"quiet": True, "ffmpeg_location": str(location)}))

    metadata = pp.get_metadata_object(str(media))

    assert any(stream["codec_type"] == "video" for stream in metadata["streams"])
    assert Path(pp.probe_executable).parent == location
