# Manual test checklist

> **This is a development checklist, not release documentation and not a user guide.**
> There is no v0.3.0. Nothing here is a published download, and nothing here describes a supported
> installation route. It exists so the project owner can test the release-shaped macOS and Windows
> applications on real hardware, because CI cannot do that.

## What changed since the last round

**Managed downloads failed on your Windows machine and now should not.** Both FFmpeg and Deno died
with `CERTIFICATE_VERIFY_FAILED`. The packaging was fine and the machine was fine: yt-dlp reached
YouTube from that same application, because it asks for certifi's certificate bundle, while our
downloader asked for nothing and fell back to whatever roots Windows had cached. Windows fills that
store lazily, and both download hosts now use roots added recently. Managed downloads now trust the
platform's roots **and** certifi's, so a private company root still works and a missing public one
no longer stops the download.

That is the one thing this build needs you to re-test, on the same machine, still without
installing FFmpeg or Deno by hand.

## What CI has and has not shown

Three kinds of evidence, kept apart on purpose. None of them substitutes for another.

| Evidence | Covers |
| --- | --- |
| Automated source tests | Ubuntu, Windows, macOS on Python 3.10–3.13 |
| Automated artifact checks | The frozen application starts, serves, logs and shuts down cleanly on all four builds — checked against the extracted archive itself |
| Automated media checks | The same VP9 + Opus MP4 converted by each frozen build to H.264 + AAC-LC; on macOS using FFmpeg installed from its real public URL |
| **Owner manual verification** | **Linux only.** A real YouTube download that played back; and a Universal conversion whose output played natively on a real iPhone in Files / Quick Look |

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
- [ ] **Retry the exact YouTube URL that previously said "Requested format is not available."**
      That failure happened with no JavaScript runtime installed, so it may simply have been a
      consequence of the missing Deno. If it still fails now that Deno works, say so — it is then
      a separate problem and will be treated as one.
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
- [ ] The compatibility decision is recorded, e.g.
      `compatibility=universal source_video_codec=… action=…` followed by
      `final_video_codec=h264 final_audio_codec=aac final_audio_profile=LC final_container=mp4`.
      A download that needed no conversion says `action=stream_copy`, which is correct and means
      the source was already compatible.
- [ ] The recent log shows a complete download lifecycle —
      `preparing → downloading → processing → completed` — with the service, media id and final
      filename on the completed record. Not a run that stops at `preparing`.
- [ ] `App data` and `Log file` point inside your own user directory.

**Is it clean?** It must contain none of:

- [ ] CI or test entries of any kind
- [ ] `pytest` paths, or any `/tmp/pytest-*`
- [ ] GitHub runner paths
- [ ] The session token, or any `X-MD-Token`
- [ ] `Cookie` or `Authorization` headers
- [ ] Credentials in a URL query string or fragment
- [ ] Unrelated environment variables

CI asserts the absence of each of these on every run, but only a report from a real desktop can
show what a real desktop produces. If anything above appears, that is a release blocker, and the
report itself is the reproduction.

---

## Reporting back

For each platform, say plainly which items passed, which failed, and quote the exact Gatekeeper
or SmartScreen wording. Attach the support report.

Until that arrives, macOS and Windows remain **unverified by a human**, and the README, the
commit history and any release notes must keep saying so.
