"""Put a freshly built FFmpeg archive through this application's own install path.

Building a binary proves it compiles. This proves the thing we would actually
ship works the way the application expects: the archive shape our extractor
looks for, the checksum gate, the executable validation, discovery preferring
the managed copy, every audio format the interface offers, and a real yt-dlp
merge.

Nothing here writes to the manifest, and nothing here is a release. The archive
is fetched from the local filesystem through the same injectable seam the
offline tests use; HTTPS remains a rule about what a *manifest* may point at,
and this archive is not in the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType

#: The member shape the Linux entry already uses. A macOS archive that does not
#: match it would need a different manifest entry, which is exactly the kind of
#: divergence worth catching before anything is pinned.
MEMBERS = MappingProxyType({"ffmpeg": "bin/ffmpeg", "ffprobe": "bin/ffprobe"})

#: The encoder universal compatibility depends on. An earlier build had no
#: H.264 encoder at all, which would have failed every conversion on macOS.
VIDEO_ENCODER = "libopenh264"

#: Every audio format the interface offers, with the encoder yt-dlp names for
#: it. FFmpeg has no native MP3 encoder and never asks for the native Opus one,
#: so a build without libmp3lame and libopus silently loses two of these.
ENCODERS = {
    "mp3": "libmp3lame",
    "opus": "libopus",
    "m4a": "aac",
    "flac": "flac",
    "wav": "pcm_s16le",
}

#: Licences that would make the binary undistributable under our terms, and
#: the libraries without which two advertised formats silently break.
FORBIDDEN = ("--enable-gpl", "--enable-nonfree")
REQUIRED = ("--enable-libmp3lame", "--enable-libopus", "--enable-libopenh264")

failures: list[str] = []


def licensing_verdict(buildconf: str) -> dict[str, bool]:
    """Judge a build purely from what the binary says it was configured with.

    Read from ``ffmpeg -buildconf`` rather than from the configure command the
    script ran, because only the binary can testify about itself. A build whose
    two records disagree is a build to throw away.
    """
    verdict = {flag: flag not in buildconf for flag in FORBIDDEN}
    verdict.update({flag: flag in buildconf for flag in REQUIRED})
    return verdict


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{f' -- {detail}' if detail else ''}")
    if not condition:
        failures.append(label)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, timeout=180)


def build_spec(archive: Path) -> object:
    from media_downloader.tools import manifest

    data = archive.read_bytes()
    return manifest.ToolSpec(
        tool="ffmpeg",
        version="locally-built",
        url=f"https://example.invalid/{archive.name}",
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        members=MEMBERS,
        licence="LGPL-2.1-or-later",
        source="packaging/ffmpeg/build-macos.sh",
    )


def install(archive: Path, root: Path, platform_name: str, machine: str) -> Path:
    """Install through ToolManager, with the archive supplied locally."""
    from media_downloader.tools.manager import ToolManager

    def fetcher(url: str, destination: Path, *, max_bytes: int) -> None:
        shutil.copyfile(archive, destination)

    manager = ToolManager(
        env={"XDG_DATA_HOME": str(root), "LOCALAPPDATA": str(root), "HOME": str(root)},
        fetcher=fetcher,
        platform_name=lambda: platform_name,
        machine=lambda: machine,
    )
    spec = build_spec(archive)
    manager.spec_for = lambda tool: spec  # type: ignore[method-assign]

    check("starts uninstalled", manager.managed_dir("ffmpeg") is None)
    installed = manager.install("ffmpeg")
    check("install completes", installed.is_dir(), str(installed))
    check("discovery reports the managed copy", manager.managed_dir("ffmpeg") is not None)
    for logical in ("ffmpeg", "ffprobe"):
        path = installed / logical
        runnable = path.is_file() and (os.access(path, os.X_OK) or os.name == "nt")
        check(f"{logical} installed and executable", runnable, str(path))
    return installed


def check_licensing(ffmpeg: Path) -> None:
    """The licensing gate, re-run against the installed copy.

    The build script already refuses to package a GPL or nonfree binary. This
    repeats the check on what came out of the archive, because that is the file
    a user would end up running.
    """
    print("\nLicensing")
    conf = run(str(ffmpeg), "-hide_banner", "-buildconf").stdout
    for flag, ok in licensing_verdict(conf).items():
        check(f"{flag} {'absent' if flag in FORBIDDEN else 'present'}", ok)


def check_encoders(ffmpeg: Path, ffprobe: Path, work: Path) -> None:
    print("\nAudio formats")
    silence = work / "tone.wav"
    run(
        str(ffmpeg),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-ac",
        "2",
        str(silence),
    )
    check("test signal produced", silence.is_file())

    for extension, encoder in ENCODERS.items():
        out = work / f"sample.{extension}"
        result = run(str(ffmpeg), "-y", "-i", str(silence), "-c:a", encoder, str(out))
        produced = out.is_file() and out.stat().st_size > 0
        check(f"{extension} via {encoder}", produced, result.stderr.strip()[-200:])
        if produced:
            probe = run(
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                str(out),
            )
            check(f"{extension} decodes back", bool(probe.stdout.strip()), probe.stderr[-200:])


def check_h264(ffmpeg: Path, ffprobe: Path, work: Path) -> None:
    """The universal target's video half, against this exact binary."""
    print("\nH.264 encoding")
    source = work / "source.mp4"
    run(
        str(ffmpeg),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x120:rate=10:duration=1",
        "-c:v",
        "mpeg4",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(source),
    )
    out = work / "h264.mp4"
    result = run(
        str(ffmpeg),
        "-y",
        "-i",
        str(source),
        "-c:v",
        VIDEO_ENCODER,
        "-b:v",
        "1M",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out),
    )
    check(f"{VIDEO_ENCODER} produced a file", out.is_file(), result.stderr.strip()[-200:])
    if out.is_file():
        probe = run(
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "csv=p=0",
            str(out),
        )
        check("it is h264 in yuv420p", probe.stdout.strip() == "h264,yuv420p", probe.stdout.strip())


def check_merge(installed: Path, work: Path) -> None:
    """A real yt-dlp merge, not an ffmpeg command that resembles one."""
    print("\nMerge")
    from yt_dlp import YoutubeDL
    from yt_dlp.postprocessor.ffmpeg import FFmpegMergerPP

    ffmpeg = installed / "ffmpeg"
    video = work / "video.mp4"
    audio = work / "audio.m4a"
    run(
        str(ffmpeg),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x120:duration=1",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(video),
    )
    run(
        str(ffmpeg),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:a",
        "aac",
        "-vn",
        str(audio),
    )
    check("separate video and audio produced", video.is_file() and audio.is_file())

    merged = work / "merged.mp4"
    ydl = YoutubeDL({"ffmpeg_location": str(installed), "quiet": True})
    info = {
        "filepath": str(merged),
        # Both keys are part of the postprocessor's contract: requested_formats
        # decides the stream mapping, __files_to_merge is what ffmpeg is fed.
        "__files_to_merge": [str(video), str(audio)],
        "requested_formats": [
            {"filepath": str(video), "vcodec": "h264", "acodec": "none", "protocol": "https"},
            {"filepath": str(audio), "acodec": "aac", "vcodec": "none", "protocol": "https"},
        ],
    }
    try:
        FFmpegMergerPP(ydl).run(info)
    except Exception as exc:
        check("yt-dlp merge runs", False, str(exc)[:200])
        return

    check("merged file produced", merged.is_file() and merged.stat().st_size > 0)
    if merged.is_file():
        probe = run(
            str(installed / "ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(merged),
        )
        kinds = set(probe.stdout.split())
        check("merged file has both streams", kinds == {"video", "audio"}, str(kinds))


def check_portability(ffmpeg: Path) -> None:
    """On macOS, a binary that links Homebrew will not run on a user's machine."""
    if sys.platform != "darwin":
        print("\nPortability\n  SKIP  otool is macOS-only")
        return
    print("\nPortability")
    linked = run("otool", "-L", str(ffmpeg)).stdout.splitlines()[1:]
    foreign = [
        line.strip()
        for line in linked
        if line.strip() and not line.strip().startswith(("/usr/lib/", "/System/Library/"))
    ]
    check("links only system libraries", not foreign, "; ".join(foreign)[:300])


def check_application_discovery(installed: Path, root: Path, tmp: Path) -> None:
    """What the application itself makes of these binaries.

    Two separate questions, and the answers differ on purpose.

    Pointed at the directory, the application's resolver must find both
    binaries -- that is the layout contract, and it must hold.

    Asked to find them on its own, it will not, because unattended discovery
    keys on the manifest and this archive is deliberately not in the manifest.
    A URL has to be durable, public and unauthenticated before it can be
    pinned, and a build artifact is none of those. Recorded here rather than
    papered over: this is the remaining gap between a verified archive and a
    macOS FFmpeg users can install.
    """
    from media_downloader.ffmpeg import detect_ffmpeg

    print("\nApplication discovery")
    explicit = detect_ffmpeg(installed)
    check(
        "the resolver finds both binaries when pointed at them",
        explicit.ffmpeg is not None and explicit.ffprobe is not None,
        f"{explicit.ffmpeg} / {explicit.ffprobe}",
    )

    saved_path = os.environ.get("PATH", "")
    saved_env = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "LOCALAPPDATA", "HOME")}
    empty = tmp / "empty"
    empty.mkdir(exist_ok=True)
    os.environ.update({"XDG_DATA_HOME": str(root), "LOCALAPPDATA": str(root), "HOME": str(root)})
    os.environ["PATH"] = str(empty)
    try:
        unattended = detect_ffmpeg().ffmpeg
    finally:
        os.environ["PATH"] = saved_path
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print(
        f"  NOTE  unattended discovery returns {unattended!r}: correct while no"
        " manifest entry exists for this platform"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--platform", default=sys.platform)
    parser.add_argument("--machine", default=None)
    args = parser.parse_args()

    archive = args.archive.resolve()
    machine = args.machine or __import__("platform").machine()
    spec_platform = {"darwin": "macos", "win32": "windows"}.get(args.platform, args.platform)

    print(f"Archive: {archive} ({archive.stat().st_size / 1e6:.1f} MB)")
    print(f"sha256:  {hashlib.sha256(archive.read_bytes()).hexdigest()}")
    print(f"Target:  {spec_platform} {machine}\n")
    print("Managed install")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "data"
        work = Path(tmp) / "work"
        work.mkdir(parents=True)
        installed = install(archive, root, spec_platform, machine)

        if not failures:
            check_licensing(installed / "ffmpeg")
            check_encoders(installed / "ffmpeg", installed / "ffprobe", work)
            check_h264(installed / "ffmpeg", installed / "ffprobe", work)
            check_merge(installed, work)
            check_portability(installed / "ffmpeg")

            check_application_discovery(installed, root, Path(tmp))

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print(json.dumps({"archive": archive.name, "result": "all checks passed"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
