#!/usr/bin/env bash
#
# Build an LGPL FFmpeg for macOS arm64, from pinned sources.
#
# Why this exists at all: macOS is the one platform with no managed FFmpeg,
# because no published provider meets the licensing and provenance bar at once.
# evermeet.cx publishes no SHA-256, and osxexperts.net publishes checksums
# behind a mutable URL with no configure line, so GPL and nonfree components
# cannot be ruled out. Building it is the only way to know what is in it.
#
# Why libmp3lame and libopus: yt-dlp names encoders explicitly -- mp3 means
# libmp3lame and opus means libopus. FFmpeg has no native MP3 encoder and is
# never asked for its native Opus one, so an FFmpeg-only build would silently
# lose two of the six formats this application offers. Neither library forces
# GPL: libmp3lame is LGPL-2.1 and libopus is BSD-3. MP3's patents expired in
# 2017.
#
# Nothing here is a release. It produces an archive and a checksum for
# inspection; adding it to the manifest is a separate, later decision that
# needs a durable public URL this does not have.
#
# Usage: packaging/ffmpeg/build-macos.sh [output-directory]

set -euo pipefail

# -- pins ---------------------------------------------------------------
#
# Every source is pinned by content, not by "latest". FFmpeg by commit, which
# fixes the entire tree; the libraries by SHA-256, each computed from the
# official download rather than copied from a third party.

FFMPEG_TAG="n9.0.1"   # the version the Linux and Windows entries already pin
FFMPEG_COMMIT="bf1b838f2ab88b4f8fd83443325c782ea0e0f7fa"
FFMPEG_REPO="https://github.com/FFmpeg/FFmpeg.git"

OPUS_VERSION="1.5.2"
OPUS_URL="https://github.com/xiph/opus/releases/download/v${OPUS_VERSION}/opus-${OPUS_VERSION}.tar.gz"
OPUS_SHA256="65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1"

LAME_VERSION="3.100"
LAME_URL="https://downloads.sourceforge.net/project/lame/lame/${LAME_VERSION}/lame-${LAME_VERSION}.tar.gz"
LAME_SHA256="ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e"

# arm64 macOS starts at 11.0, so nothing older can run this anyway.
export MACOSX_DEPLOYMENT_TARGET=11.0

OUT_DIR="$(cd "${1:-dist-ffmpeg}" 2>/dev/null && pwd || (mkdir -p "${1:-dist-ffmpeg}" && cd "${1:-dist-ffmpeg}" && pwd))"
WORK="$(mktemp -d)"
DEPS="$WORK/deps"
mkdir -p "$DEPS"
trap 'rm -rf "$WORK"' EXIT

JOBS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"

say() { printf '\n== %s\n' "$*"; }

#: Long build steps are quiet because their output is thousands of lines of
#: compiler noise. Quiet must not mean undiagnosable, though: the first attempt
#: at this build failed at FFmpeg's configure and the log contained nothing at
#: all about why, because configure prints its diagnosis to stdout.
LOG_TAIL_LINES=60

quietly() {
  local label="$1"; shift
  local log="$WORK/${label}.log"
  if ! "$@" >"$log" 2>&1; then
    echo "  FAILED: $label" >&2
    echo "  last $LOG_TAIL_LINES line(s) of $log:" >&2
    tail -n "$LOG_TAIL_LINES" "$log" | sed 's/^/    | /' >&2
    # FFmpeg records the actual compile failure here rather than in configure's
    # own output, and it is the only place that says what was really missing.
    if [ -f "$PWD/ffbuild/config.log" ]; then
      echo "  last $LOG_TAIL_LINES line(s) of ffbuild/config.log:" >&2
      tail -n "$LOG_TAIL_LINES" "$PWD/ffbuild/config.log" | sed 's/^/    | /' >&2
    fi
    exit 1
  fi
}

fetch() {
  local url="$1" want="$2" dest="$3"
  curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 -o "$dest" "$url"
  local got
  got="$(shasum -a 256 "$dest" | awk '{print $1}')"
  if [ "$got" != "$want" ]; then
    echo "checksum mismatch for $url" >&2
    echo "  expected $want" >&2
    echo "  got      $got" >&2
    exit 1
  fi
  echo "  verified $(basename "$dest")"
}

# -- libopus ------------------------------------------------------------

say "libopus $OPUS_VERSION"
fetch "$OPUS_URL" "$OPUS_SHA256" "$WORK/opus.tar.gz"
tar -xzf "$WORK/opus.tar.gz" -C "$WORK"
(
  cd "$WORK/opus-$OPUS_VERSION"
  quietly opus-configure ./configure --prefix="$DEPS" --disable-shared \
    --enable-static --disable-doc --disable-extra-programs --with-pic
  quietly opus-make make -j"$JOBS"
  quietly opus-install make install
)

# -- LAME ---------------------------------------------------------------
#
# --disable-frontend: only the library is wanted, and lame's command-line
# frontend is the part that most often fails to build on current toolchains.

say "LAME $LAME_VERSION"
fetch "$LAME_URL" "$LAME_SHA256" "$WORK/lame.tar.gz"
tar -xzf "$WORK/lame.tar.gz" -C "$WORK"
(
  cd "$WORK/lame-$LAME_VERSION"
  quietly lame-configure ./configure --prefix="$DEPS" --disable-shared \
    --enable-static --disable-frontend --disable-dependency-tracking --with-pic
  quietly lame-make make -j"$JOBS"
  quietly lame-install make install
)

# -- FFmpeg -------------------------------------------------------------

say "FFmpeg $FFMPEG_TAG"
git clone --quiet --depth 1 --branch "$FFMPEG_TAG" "$FFMPEG_REPO" "$WORK/ffmpeg"
ACTUAL_COMMIT="$(git -C "$WORK/ffmpeg" rev-parse HEAD)"
if [ "$ACTUAL_COMMIT" != "$FFMPEG_COMMIT" ]; then
  echo "FFmpeg tag $FFMPEG_TAG does not point at the pinned commit" >&2
  echo "  expected $FFMPEG_COMMIT" >&2
  echo "  got      $ACTUAL_COMMIT" >&2
  exit 1
fi
echo "  commit $ACTUAL_COMMIT verified"

# --disable-autodetect is the portability control: without it FFmpeg links
# whatever Homebrew happens to have on the runner, and the result does not run
# on a user's machine. Everything enabled below is either built above or part
# of macOS itself.
#
# Deliberately absent: --enable-gpl and --enable-nonfree. Nothing here needs
# them, and their absence is what keeps the result LGPL. The gate below fails
# the build if either ever appears.
# libopus is found through pkg-config, so the staged prefix has to be on
# PKG_CONFIG_PATH -- and set, not appended: the runner arrives with its own
# value, and inheriting it would reintroduce exactly the Homebrew leakage that
# --disable-autodetect exists to prevent. LAME needs no entry here; FFmpeg
# checks for it by linking directly, which the extra cflags and ldflags cover.
export PKG_CONFIG_PATH="$DEPS/lib/pkgconfig"

(
  cd "$WORK/ffmpeg"
  quietly ffmpeg-configure ./configure \
    --prefix="$WORK/install" \
    --disable-autodetect \
    --extra-cflags="-I$DEPS/include" \
    --extra-ldflags="-L$DEPS/lib" \
    --pkg-config-flags="--static" \
    --enable-libmp3lame \
    --enable-libopus \
    --enable-zlib \
    --enable-securetransport \
    --disable-shared \
    --enable-static \
    --disable-doc \
    --disable-debug \
    --disable-ffplay
  quietly ffmpeg-make make -j"$JOBS"
  quietly ffmpeg-install make install
)

FFMPEG_BIN="$WORK/install/bin/ffmpeg"
FFPROBE_BIN="$WORK/install/bin/ffprobe"

# -- licensing gate -----------------------------------------------------
#
# Asserted against the built binary's own record of how it was configured, not
# against the command above. If these ever disagree, the binary wins.

say "Licensing gate"
BUILDCONF="$("$FFMPEG_BIN" -hide_banner -buildconf)"
fail=0
for forbidden in --enable-gpl --enable-nonfree; do
  if grep -q -- "$forbidden" <<<"$BUILDCONF"; then
    echo "  REFUSING TO PACKAGE: $forbidden is present" >&2
    fail=1
  else
    echo "  absent:  $forbidden"
  fi
done
for required in --enable-libmp3lame --enable-libopus; do
  if grep -q -- "$required" <<<"$BUILDCONF"; then
    echo "  present: $required"
  else
    echo "  MISSING: $required -- two audio formats would silently break" >&2
    fail=1
  fi
done
[ "$fail" -eq 0 ] || exit 1

"$FFMPEG_BIN" -hide_banner -L | head -20

# -- portability gate ---------------------------------------------------

say "Portability gate"
FOREIGN="$(otool -L "$FFMPEG_BIN" | tail -n +2 | awk '{print $1}' \
  | grep -v '^/usr/lib/' | grep -v '^/System/Library/' || true)"
if [ -n "$FOREIGN" ]; then
  echo "  REFUSING TO PACKAGE: links non-system libraries:" >&2
  echo "$FOREIGN" >&2
  exit 1
fi
echo "  links only system libraries"

# -- functional smokes --------------------------------------------------
#
# "LGPL-only" must not quietly mean "less capable", so every format the
# interface offers is encoded for real. A merge too, since that is what runs
# on nearly every video download.

say "Functional smokes"
SMOKE="$WORK/smoke"
mkdir -p "$SMOKE"
"$FFMPEG_BIN" -v error -y -f lavfi -i "sine=frequency=440:duration=1" -ac 2 "$SMOKE/tone.wav"
for pair in "mp3:libmp3lame" "opus:libopus" "m4a:aac" "flac:flac" "wav:pcm_s16le"; do
  ext="${pair%%:*}"; enc="${pair##*:}"
  "$FFMPEG_BIN" -v error -y -i "$SMOKE/tone.wav" -c:a "$enc" "$SMOKE/out.$ext"
  [ -s "$SMOKE/out.$ext" ] || { echo "  $ext produced nothing" >&2; exit 1; }
  echo "  $ext via $enc  OK"
done

"$FFMPEG_BIN" -v error -y -f lavfi -i "testsrc=size=160x120:duration=1" \
  -pix_fmt yuv420p -an "$SMOKE/v.mp4"
"$FFMPEG_BIN" -v error -y -f lavfi -i "sine=frequency=440:duration=1" \
  -c:a aac -vn "$SMOKE/a.m4a"
"$FFMPEG_BIN" -v error -y -i "$SMOKE/v.mp4" -i "$SMOKE/a.m4a" -c copy "$SMOKE/merged.mp4"
STREAMS="$("$FFPROBE_BIN" -v error -show_entries stream=codec_type -of csv=p=0 "$SMOKE/merged.mp4" | sort | tr '\n' ' ')"
[ "$STREAMS" = "audio video " ] || { echo "  merge produced: $STREAMS" >&2; exit 1; }
echo "  merge  OK"

# -- package ------------------------------------------------------------
#
# bin/ffmpeg and bin/ffprobe: the same member shape the Linux entry already
# uses, so a future manifest entry needs no special case.

say "Packaging"
STAGE="$WORK/stage"
mkdir -p "$STAGE/bin"
cp "$FFMPEG_BIN" "$FFPROBE_BIN" "$STAGE/bin/"
strip -S "$STAGE/bin/ffmpeg" "$STAGE/bin/ffprobe" 2>/dev/null || true
chmod 755 "$STAGE/bin/ffmpeg" "$STAGE/bin/ffprobe"

ARCH="$(uname -m)"
ARCHIVE="$OUT_DIR/ffmpeg-${FFMPEG_TAG}-macos-${ARCH}-lgpl.tar.xz"
tar -cJf "$ARCHIVE" -C "$STAGE" bin

SHA="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
SIZE="$(wc -c <"$ARCHIVE" | tr -d ' ')"

cat >"$OUT_DIR/ffmpeg-build-info.json" <<JSON
{
  "archive": "$(basename "$ARCHIVE")",
  "sha256": "$SHA",
  "size_bytes": $SIZE,
  "members": {"ffmpeg": "bin/ffmpeg", "ffprobe": "bin/ffprobe"},
  "licence": "LGPL-2.1-or-later",
  "platform": "macos",
  "architecture": "$ARCH",
  "deployment_target": "$MACOSX_DEPLOYMENT_TARGET",
  "ffmpeg": {"tag": "$FFMPEG_TAG", "commit": "$ACTUAL_COMMIT"},
  "libopus": {"version": "$OPUS_VERSION", "sha256": "$OPUS_SHA256"},
  "lame": {"version": "$LAME_VERSION", "sha256": "$LAME_SHA256"},
  "ffmpeg_version": "$("$FFMPEG_BIN" -hide_banner -version | head -1)",
  "configuration": $(printf '%s' "$BUILDCONF" | tail -n +2 | tr '\n' ' ' | sed 's/  */ /g;s/^ //;s/ $//' | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
}
JSON

say "Done"
echo "  archive $ARCHIVE"
echo "  sha256  $SHA"
echo "  size    $SIZE bytes"
echo
echo "Not added to the manifest: a pinned URL has to be durable, public and"
echo "unauthenticated, and a build artifact is none of those."
