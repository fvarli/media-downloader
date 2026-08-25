"""The postprocessor that carries out the compatibility verdict.

The decision itself is tested in test_compatibility.py. What matters here is
everything around it: that a half-converted file never takes the finished
name, that a conversion which did not achieve the target is rejected rather
than reported as success, and that nothing is left behind when it fails.

Offline throughout -- FFmpeg and ffprobe are replaced on the instance, so the
control flow is exercised without encoding anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from media_downloader.normalize import TEMP_SUFFIX, make_universal_postprocessor

MP4_FORMAT = "mov,mp4,m4a,3gp,3g2,mj2"


def probe_json(video: str = "h264", audio: str | None = "aac", profile: str = "LC") -> dict:
    streams: list[dict[str, Any]] = [
        {"codec_type": "video", "codec_name": video, "pix_fmt": "yuv420p"}
    ]
    if audio is not None:
        streams.append({"codec_type": "audio", "codec_name": audio, "profile": profile})
    return {"streams": streams, "format": {"format_name": MP4_FORMAT}}


class Harness:
    """A postprocessor with FFmpeg and ffprobe replaced."""

    def __init__(self, *, before: dict, after: dict, produce: bool = True) -> None:
        self.pp = make_universal_postprocessor()
        self.commands: list[list[str]] = []
        self.outputs: list[Path] = []
        self.produce = produce
        self._probes = [before, after]
        self.pp.get_metadata_object = self._probe  # type: ignore[method-assign]
        self.pp.real_run_ffmpeg = self._ffmpeg  # type: ignore[method-assign]

    def _probe(self, path: str, opts: Any = None) -> dict:
        # The first probe reads the source, the second the produced file.
        return self._probes[0] if len(self._probes) > 1 else self._probes[-1]

    def _ffmpeg(self, inputs: list, outputs: list) -> None:
        self._probes.pop(0)
        self.commands.append(list(outputs[0][1]))
        self.outputs.append(Path(outputs[0][0]))
        if self.produce:
            Path(outputs[0][0]).write_bytes(b"converted media")

    def run(self, source: Path) -> dict:
        info: dict[str, Any] = {"filepath": str(source)}
        _, updated = self.pp.run(info)
        return updated


def test_a_converted_file_takes_the_finished_name(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")

    info = Harness(before=probe_json("vp9"), after=probe_json()).run(source)

    assert Path(info["filepath"]) == source
    assert source.read_bytes() == b"converted media"
    assert info["ext"] == "mp4"


def test_a_different_container_is_renamed_to_mp4(tmp_path: Path) -> None:
    source = tmp_path / "clip.webm"
    source.write_bytes(b"original")

    harness = Harness(before=probe_json("vp9"), after=probe_json())
    info = harness.run(source)

    assert Path(info["filepath"]) == tmp_path / "clip.mp4"
    assert (tmp_path / "clip.mp4").is_file()


def test_a_failed_conversion_leaves_nothing_behind(tmp_path: Path) -> None:
    """No zero-byte file, and no temporary one, pretending to be a download."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")

    harness = Harness(before=probe_json("vp9"), after=probe_json(), produce=False)
    with pytest.raises(Exception, match="produced no output"):
        harness.run(source)

    assert source.read_bytes() == b"original"
    assert list(tmp_path.glob(f"*{TEMP_SUFFIX}")) == []


def test_a_conversion_that_missed_the_target_is_refused(tmp_path: Path) -> None:
    """FFmpeg exiting zero is not evidence a phone will play the file -- which
    is exactly the assumption that let an MP4 full of VP9 through."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")

    # FFmpeg "succeeds" but the result is still VP9.
    harness = Harness(before=probe_json("vp9"), after=probe_json("vp9"))
    with pytest.raises(Exception, match="did not meet the compatibility target"):
        harness.run(source)

    assert source.read_bytes() == b"original"
    assert list(tmp_path.glob(f"*{TEMP_SUFFIX}")) == []


def test_the_original_survives_a_refused_conversion(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")
    harness = Harness(before=probe_json("vp9"), after=probe_json(audio="opus", profile=""))
    with pytest.raises(Exception, match="audio codec is opus"):
        harness.run(source)
    assert source.is_file()
    assert source.read_bytes() == b"original"


def test_an_already_compatible_file_is_copied_not_encoded(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")

    harness = Harness(before=probe_json(), after=probe_json())
    harness.run(source)

    args = harness.commands[0]
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "copy"
    assert "libx264" not in args


def test_the_conversion_is_written_beside_the_target(tmp_path: Path) -> None:
    """A rename within one directory, so promotion is atomic rather than a copy
    across filesystems that could be interrupted half way."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")

    harness = Harness(before=probe_json("vp9"), after=probe_json())
    harness.run(source)

    written = harness.outputs[0]
    assert written.parent == source.parent
    assert written.name.endswith(TEMP_SUFFIX)
    assert written != source


def test_a_missing_file_is_left_alone(tmp_path: Path) -> None:
    pp = make_universal_postprocessor()
    info: dict[str, Any] = {"filepath": str(tmp_path / "gone.mp4")}
    leftovers, returned = pp.run(info)
    assert leftovers == []
    assert returned is info


def test_no_output_path_is_left_alone() -> None:
    pp = make_universal_postprocessor()
    leftovers, returned = pp.run({})
    assert leftovers == []
    assert returned == {}
