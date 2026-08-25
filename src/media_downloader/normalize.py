"""Normalising a finished download to something that plays anywhere.

The decision itself lives in :mod:`media_downloader.compatibility`, which is
pure and knows nothing about yt-dlp or FFmpeg. This module is the thin layer
that feeds it real data and carries out its verdict.

It runs as a yt-dlp postprocessor rather than a step of our own, so it inherits
what yt-dlp already solved: locating the FFmpeg the user configured, running it
with an argument list instead of a shell string, and reporting the phase through
the postprocessor hook the web interface already listens to.

Two rules shape everything here. Only what is genuinely incompatible is
re-encoded, because re-encoding a file that already plays costs time and a
generation of quality for nothing. And the *result* is inspected before it is
accepted -- FFmpeg exiting zero is not evidence that a phone will play the
file, which is exactly the mistake that let an MP4 full of VP9 through.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from media_downloader.compatibility import (
    MediaProbe,
    ffmpeg_output_arguments,
    plan_for,
    validate_universal,
)
from media_downloader.logging_setup import get_logger

logger = get_logger("compatibility")

#: Suffix for the in-progress conversion. It sits beside the target so the
#: promotion below is a rename within one filesystem rather than a copy.
TEMP_SUFFIX = ".compat-tmp.mp4"


def make_universal_postprocessor() -> Any:
    """Build the postprocessor that enforces the universal target.

    Imported lazily, like the automatic-naming postprocessor, so ``--help`` does
    not pay for yt-dlp's import.
    """
    from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor
    from yt_dlp.utils import PostProcessingError

    class UniversalCompatibilityPP(FFmpegPostProcessor):  # type: ignore[misc]
        """Bring a downloaded video to H.264 + AAC-LC in MP4, and prove it."""

        def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
            source = info.get("filepath")
            if not source:
                logger.debug("No output path to normalise; leaving the file alone.")
                return [], info

            source_path = Path(source)
            if not source_path.is_file():
                logger.debug("Output path does not exist; leaving the file alone.")
                return [], info

            probe = MediaProbe.from_ffprobe(self.get_metadata_object(str(source_path)))
            plan = plan_for(probe)
            logger.info(
                "compatibility=universal source_video_codec=%s source_audio_codec=%s%s action=%s",
                plan.source_video_codec or "none",
                plan.source_audio_codec or "none",
                f" source_audio_profile={plan.source_audio_profile}"
                if plan.source_audio_profile
                else "",
                plan.action,
            )

            # Even an all-copy plan is remuxed: it costs no quality, and it is
            # the only way to be sure the container is MP4 with faststart.
            target = source_path.with_suffix(".mp4")
            temporary = source_path.with_name(source_path.stem + TEMP_SUFFIX)

            try:
                self.real_run_ffmpeg(
                    [(str(source_path), [])],
                    [(str(temporary), ffmpeg_output_arguments(plan))],
                )
                verdict = self._accept(temporary)
            except Exception:
                # Never leave a half-written file behind that could be mistaken
                # for the download.
                temporary.unlink(missing_ok=True)
                raise

            # Atomic: the finished name only ever refers to a validated file.
            temporary.replace(target)
            logger.info("%s", verdict.as_log_fields())

            info["filepath"] = str(target)
            info["ext"] = "mp4"
            # Hand the superseded source back for deletion when the conversion
            # changed the extension; otherwise it has already been replaced.
            leftovers = [str(source_path)] if target != source_path else []
            return leftovers, info

        def _accept(self, produced: Path) -> Any:
            """Judge what FFmpeg actually produced, not that it exited zero."""
            if not produced.is_file() or produced.stat().st_size == 0:
                raise PostProcessingError("Compatibility conversion produced no output file.")

            verdict = validate_universal(
                MediaProbe.from_ffprobe(self.get_metadata_object(str(produced)))
            )
            if not verdict.ok:
                raise PostProcessingError(
                    "The converted file did not meet the compatibility target: "
                    + "; ".join(verdict.problems)
                )
            return verdict

    return UniversalCompatibilityPP()


def register_universal_compatibility(ydl: Any) -> None:
    """Attach the normaliser, if this yt-dlp object can take postprocessors.

    Guarded exactly like the automatic-naming registration: an injected test
    double without ``add_post_processor`` degrades to doing nothing rather than
    raising.
    """
    add = getattr(ydl, "add_post_processor", None)
    if not callable(add):
        logger.debug("This yt-dlp object cannot take postprocessors; skipping normalisation.")
        return
    add(make_universal_postprocessor(), when="post_process")
