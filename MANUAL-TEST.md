# Manual test checklist

> **This is a development checklist, not release documentation and not a user guide.**
> There is no v0.3.0. Nothing here is a published download, and nothing here describes a supported
> installation route. It exists so the project owner can test the release-shaped macOS and Windows
> applications on real hardware, because CI cannot do that.

## What CI has and has not shown

Three kinds of evidence, kept apart on purpose:

| Evidence | Covers |
| --- | --- |
| Automated source tests | Ubuntu, Windows, macOS on Python 3.10–3.13 |
| Automated artifact checks | The frozen application starts, serves, logs and shuts down cleanly on all three platforms — checked against the extracted archive itself |
| Owner manual verification | **Linux only.** A real YouTube download that played back, from the frozen artifact |

A green CI run is not a person double-clicking an application. Everything below is the part no
runner can do: Gatekeeper, SmartScreen, whether a terminal window appears, and whether media
actually downloads and plays.

## Get the artifacts

Built by the **Standalone build** workflow. Each run produces, per platform, the archive itself
plus a small metadata artifact holding `artifact-info.json` and a `.sha256` file.

| Platform | Archive |
| --- | --- |
| macOS arm64 | `Media-Downloader-macOS-arm64-windowed.zip` |
| Windows x64 | `Media-Downloader-Windows-x64-windowed.zip` |
| Linux x86_64 | `Media-Downloader-Linux-x86_64.tar.gz` |

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
- [ ] Download an **Instagram** item
- [ ] Download an **X/Twitter** item
- [ ] Download a **YouTube** item
- [ ] **Video — Best** produces a file that plays
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
- [ ] Download an **Instagram** item
- [ ] Download an **X/Twitter** item
- [ ] Download a **YouTube** item
- [ ] **Video — Best** produces a file that plays
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
- [ ] `FFmpeg` states the real source and version. On macOS with no FFmpeg installed, the
      application should report it as unsupported and offer no install.
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
