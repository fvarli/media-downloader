# Manual test checklist

> **This is a development checklist, not release documentation and not a user guide.**
> There is no v0.3.0. Nothing here is a published download, and nothing here describes a supported
> installation route. It exists so the project owner can test the release-shaped macOS and Windows
> applications on real hardware, because CI cannot do that.

## What changed since the last round

Windows and macOS are both verified now, so this round is release hardening rather than a bug hunt.
Nothing about how downloading works has changed; the fixes are about what the build contains, what a
support report reveals, and one case where the application described its own result wrongly.

**A support report no longer carries your account name.** Reports were showing
`C:\Users\<you>\AppData\Local\…` and `/Users/<you>/Library/…` throughout, and those get pasted
into issues and emails. The home directory is now replaced with `<home>` in anything you export or
copy, keeping the rest of the path intact so it is still worth reading. **The log file on your own
disk is unchanged** and still holds real paths — that is deliberate, since it is what you debug with.

**Universal can no longer report audio it did not produce.** One earlier run of a video finished as
`selection=video_plus_audio` while the file itself had no sound. Those were two different facts:
what the site said it was sending, and what `ffprobe` found in the finished file. Nothing compared
them, so the "downloaded without audio" warning never appeared. The file is now the one that counts.

**mutagen is out of the builds.** yt-dlp's packaging hook was compiling it in, and it is
GPL-2.0-or-later — copyleft inside an MIT binary, for a feature this application never uses.

**The builds now carry their licences**, and the macOS app finally has a real bundle identifier and
reports its own version instead of `0.0.0`.

## What CI has and has not shown

Three kinds of evidence, kept apart on purpose. None of them substitutes for another.

| Evidence | Covers |
| --- | --- |
| Automated source tests | Ubuntu, Windows, macOS on Python 3.10–3.13 |
| Automated artifact checks | The frozen application starts, serves, logs and shuts down cleanly on all four builds — checked against the extracted archive itself |
| Automated media checks | The same VP9 + Opus MP4 converted by each frozen build to H.264 + AAC-LC, **using the managed FFmpeg the application discovers for itself**, with no FFmpeg on PATH |
| **Owner manual verification** | **Linux, Windows and macOS arm64.** Launch, managed FFmpeg and Deno installing and persisting across a restart, YouTube extraction, Original completing, and Universal producing H.264 + AAC-LC MP4 that plays natively with picture and sound |

> The media check used to put the managed FFmpeg on PATH before running, which let the application
> find it by name and skip its own discovery entirely. That is why it stayed green through a bug
> that made Universal unusable on a real Windows machine: it was testing that the frozen binary can
> run an FFmpeg somebody else provided. The runners ship no FFmpeg of their own, so leaving the
> managed one off PATH reproduces the machine that failed; the check now also asserts that the
> binary which did the work came from the managed install.

A green CI run is not a person double-clicking an application. Everything below is the part no
runner can do: Gatekeeper, SmartScreen, whether a terminal window appears, whether FFmpeg installs
from a real browser session, and whether media actually downloads and plays.

## Get the artifacts

Built by the **Standalone build** workflow. Each run produces, per platform, the archive itself
plus a small metadata artifact holding `artifact-info.json` and a `.sha256` file.

| Platform | Archive | Use |
| --- | --- | --- |
| macOS arm64 | `Media-Downloader-macOS-arm64-windowed.zip` | the one to test |
| Windows x64 | `Media-Downloader-Windows-x64-windowed.zip` | the one to test |
| Windows x64 | `Media-Downloader-Windows-x64-console.zip` | diagnostics only, see below |
| Linux x86_64 | `Media-Downloader-Linux-x86_64.tar.gz` | the one to test |

> **Extract the whole archive.** `media-downloader.exe` cannot run without the `_internal\`
> folder beside it; separating them gives *"Failed to load Python DLL …\_internal\python312.dll"*.
> That message means the layout is wrong, not that anything is missing from the download.

> **The console build is a diagnostic tool, not the application.** It is the same commit and the
> same spec, built with a console attached. If the windowed build ever does nothing at all, run
> the console build from a terminal and send what it prints. Note that its behaviour differs on
> purpose: run with no arguments it prints a usage message, while the windowed build opens the
> interface.

The archives are packed on their own native runners and uploaded as single files, so what
downloads is exactly the archive — no wrapper zip, one extraction. CI verifies that the uploaded
file is byte-for-byte the one it tested.

> ### Download through the browser, not the CLI
>
> This decides whether two of the checks below are meaningful at all. Gatekeeper and SmartScreen
> only engage on files carrying macOS's quarantine attribute or Windows' Mark-of-the-Web, and
> **a browser download sets those while `gh run download` does not.** Fetching with the CLI makes
> the first-launch prompts silently not appear, which looks like a pass and proves nothing.
>
> `gh run download` also unpacks the archive for you, so you never hold the file itself and
> cannot check its SHA-256. Both were confirmed: the browser download is the archive
> byte-for-byte, with the bundle executable stored at mode 755.

Open the workflow run on GitHub → **Artifacts** → click the archive name.

Confirm what you received matches what CI built — `artifact-info.json` records the commit,
version, OS, architecture, Python, PyInstaller, bundled yt-dlp and windowed/console mode:

```bash
# macOS / Linux
shasum -a 256 Media-Downloader-macOS-arm64-windowed.zip
```

```powershell
# Windows
Get-FileHash Media-Downloader-Windows-x64-windowed.zip -Algorithm SHA256
```

---

## macOS arm64 (windowed)

- [ ] Unzip `Media-Downloader-macOS-arm64-windowed.zip` in Finder
- [ ] The result is `Media Downloader.app` and nothing else
- [ ] **Gatekeeper**: double-click. The app is unsigned and un-notarized, so macOS is expected to
      refuse it. Record the exact wording shown.
- [ ] **Right-click → Open**, then confirm at the prompt. Record whether this is required, and
      whether it is enough.
- [ ] **No terminal or console window appears at any point**
- [ ] The default browser opens on its own
- [ ] The Web UI loads at `127.0.0.1` and reports no error
- [ ] **FFmpeg status**: unless you already have one on your PATH, the interface should now offer
      to install it. This is the new part — macOS had no managed FFmpeg until now.
- [ ] **Install FFmpeg through the interface** and confirm it succeeds. It downloads roughly 16 MB
      from this repository's own `ffmpeg-n9.0.1-macos-arm64-1` release, checks it against a
      checksum built into the application, and installs it privately — never onto your PATH.
- [ ] Download an **Instagram** item
- [ ] Download an **X/Twitter** item
- [ ] Download a **YouTube** item
- [ ] **Video — Universal** produces a file that plays. This is the default and it may spend a
      while on "Optimising compatibility" after the download; that is the conversion, not a hang.
- [ ] **Audio — MP3** produces a file that plays
- [ ] **Open Downloads Folder** opens the right directory and the files are in it
- [ ] Paste an invalid URL: the error is understandable and the app stays usable
- [ ] **Export Support Report**, then read it (below)
- [ ] Quit the application
- [ ] Relaunch: it starts again, with no leftover state and no port complaint

## Windows x64 (windowed)

- [ ] Unzip `Media-Downloader-Windows-x64-windowed.zip` in Explorer
- [ ] The result is a single `media-downloader\` folder containing `media-downloader.exe`
      beside `_internal\`
- [ ] **`media-downloader.exe` is the intended entry point** — double-click it, nothing else
- [ ] **SmartScreen**: record the exact wording shown
- [ ] **More info → Run anyway**. Record whether this is required.
- [ ] **No console window appears at any point**
- [ ] The default browser opens on its own
- [ ] The Web UI loads at `127.0.0.1` and reports no error
- [ ] **FFmpeg is initially unavailable** — expected, nothing has been installed by hand
- [ ] **Install FFmpeg through the interface.** This is the check that failed last time. It must
      download over verified HTTPS, pass its checksum, extract, and become available — with no
      certificate error.
- [ ] **Deno is initially unavailable**, then **install it through the interface** too. Same
      requirement: verified HTTPS, checksum, extract, available.
- [ ] **Retry the exact YouTube URL that failed, in Universal.** This is the check this round
      exists for: it must reach **Completed**, and the file must play in the Windows native player
      and on an iPhone. Reaching "Failed" after leaving a file behind is the bug, not a pass.
- [ ] The same URL in **Original** still completes, as it did last time. Its `.webm` not playing
      natively is expected and is not a failure.
- [ ] Download an **Instagram** item
- [ ] Download an **X/Twitter** item
- [ ] Download a **YouTube** item
- [ ] **Video — Universal** produces a file that plays
- [ ] **Audio — MP3** produces a file that plays
- [ ] **Open Downloads Folder** opens the right directory and the files are in it
- [ ] Paste an invalid URL: the error is understandable and the app stays usable
- [ ] **Export Support Report**, then read it (below)
- [ ] Close the application
- [ ] Relaunch: it starts again, with no leftover state and no port complaint

---

## Reading the support report

The report is the evidence that matters most here, because it is what a real user would send to a
stranger. Two questions.

**Is it correct?**

- [ ] `JS runtime` names the runtime actually on that machine, with a matching version —
      `system node 22.20.0`, `managed deno 2.9.5`, or `unavailable`. A name from one program
      beside a version from another is the bug this replaced.
- [ ] `FFmpeg` states the real source and version — `managed n9.0.1` after installing it through
      the interface, or a system path if you already had one. It must not say unsupported: every
      platform now has a verified source, and unsupported would mean you could not get FFmpeg.
- [ ] `HTTPS trust` names its sources, e.g. `system + certifi 2026.07.22`. If it says only
      `system`, the certificate bundle did not load and the download problem would return.
- [ ] There is no TLS or certificate error anywhere in the report.
- [ ] `compatibility ffmpeg=… ffprobe=…` names the binaries the conversion used. On a machine
      with no system FFmpeg both must point inside `App data`, at the managed install — a bare
      `ffmpeg` or `ffprobe` with no directory is the fault this round fixed.
- [ ] The compatibility decision is recorded, e.g.
      `compatibility=universal source_video_codec=… action=…` followed by
      `final_video_codec=h264 final_audio_codec=aac final_audio_profile=LC final_container=mp4`.
      A download that needed no conversion says `action=stream_copy`, which is correct and means
      the source was already compatible.
- [ ] The recent log shows a complete download lifecycle —
      `preparing → downloading → processing → completed` — with the service, media id and final
      filename on the completed record. Not a run that stops at `preparing`.
- [ ] `App data` and `Log file` read `<home>/…`, not your account name. This is the one item that
      is new this round: the exported report must not contain your username anywhere, while the
      rest of each path stays readable.

**Is it clean?** It must contain none of:

- [ ] CI or test entries of any kind
- [ ] `pytest` paths, or any `/tmp/pytest-*`
- [ ] GitHub runner paths
- [ ] The session token, or any `X-MD-Token`
- [ ] `Cookie` or `Authorization` headers
- [ ] Credentials in a URL query string or fragment
- [ ] Unrelated environment variables
- [ ] Your account name, in any path

CI asserts the absence of each of these on every run, but only a report from a real desktop can
show what a real desktop produces. If anything above appears, that is a release blocker, and the
report itself is the reproduction.

---

## Reporting back

For each platform, say plainly which items passed, which failed, and quote the exact Gatekeeper
or SmartScreen wording. Attach the support report.

Until that arrives, macOS and Windows remain **unverified by a human**, and the README, the
commit history and any release notes must keep saying so.
