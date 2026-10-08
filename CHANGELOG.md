# Changelog

All notable changes to WinSpector Pro. Full release notes (in Russian) are on the
[Releases](https://github.com/deeCaTofficial/WinSpectorPro/releases) page.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.1.0] — 2026-10-08

A major update. Cleanup finds more and deletes only what programs recreate on their own;
leftovers of uninstalled programs are now found and quarantined; the interface is redesigned,
with a new logo, an English UI, a Settings window and a card-based report.

### Added

- **Leftovers of uninstalled programs.** Folders with settings, logs and caches left by
  uninstallers are found only with evidence that the program is gone (ghost uninstall entry,
  broken shortcut, dead path in the registry or in execution traces). They are moved to a
  30-day quarantine in `%LOCALAPPDATA%\WinSpectorPro\Quarantine` and can be restored.
- **Installer leftovers:** orphaned copies in `C:\Windows\Installer` and `Package Cache`
  (checked only with administrator rights, when Windows reports the full list of installed
  products) and previous app versions left by Squirrel updaters (Discord, Figma, …).
- **Empty program folders** left in `Program Files`, `ProgramData` and `AppData` are removed
  in the final pass, after files.
- **More cleanup targets:** caches of Chrome, Edge, Firefox and Yandex in all profiles,
  Telegram, Discord, Figma, Yandex Music and other apps, game logs and crash reports, old
  shader cache, icon cache.
- **Dry run for developers:** `scripts/audit_cleanup.py` shows what would be deleted, what
  would be skipped and why, without deleting anything.
- **Key check in the setup window:** the Gemini key is verified with one short request before
  it is saved, so an invalid key, an unavailable model or an unsupported country is reported
  right away instead of silently falling back to the mode without AI.
- **English interface.** The app and its report are available in Russian and English; the
  Windows display language is picked on first launch and can be changed in Settings.
- **Settings window:** interface language, update check and reports folder; adding, replacing
  or removing the Gemini key; quarantine with restore of selected leftovers.
- **Update check.** After the window opens, the app asks the GitHub releases API for the latest
  version and shows a notice when a newer one exists. Nothing is downloaded or installed
  automatically.
- **SHA-256 for release files:** `scripts/build.py` writes `WinSpectorPro.exe.sha256`.
- **Fewer antivirus false positives:** the release EXE is built with a PyInstaller bootloader
  compiled from the official sources (`scripts/build_bootloader.py`) instead of the stock one
  that some engines flag in every PyInstaller app; UPX compression is off and pywin32 tests and
  demos are no longer bundled.
- **Documentation in English and Russian** (`README.md` / `README_RU.md`, user guide in both
  languages), a Safety & Transparency section, and this changelog.

### Changed

- The main screen opens right away: the Gemini key is no longer requested at first launch and is
  connected from the main screen or **Settings → Gemini**.
- **New minimalist interface.** One calm dark theme for all windows, a single **Optimize**
  button and a step list during optimization.
  - **Custom title bar** with the settings button that blends into the background. The window
    still behaves like any Windows window: resizes by its edges, snaps to screen edges and
    shows Windows 11 snap layouts over the maximize button.
  - **Messages and confirmations** use the same style and name the action on their buttons
    (**Stop and close** instead of **Yes**); for irreversible actions Enter picks the safe
    choice.
  - **Report as a page of cards** instead of a wall of text: totals (freed space, empty
    folders, system changes); which apps were running and how much space waits for them to
    close, under readable names (Visual Studio Code, not `chromium_app_caches: Code`); what
    was changed; what went to quarantine; Gemini's notes or why the run was without AI. The
    Markdown report is still saved and copied as before.
  - **Dot-grid background** instead of particles: a soft wave spreads from the logo every few
    seconds and more often during optimization; the grid freezes on the report and stays
    still when Windows animations are turned off.
- **New logo:** a W whose last stroke turns into a check mark. The icon now contains every
  size from 16 to 256 px with a separate, bolder drawing for the smallest ones, so it stays
  sharp in the taskbar and in Alt+Tab. Sources are in `assets/logo.svg`;
  `scripts/make_logo.py` rebuilds the icon and the README logo.
- Only regenerable data is deleted: caches of running programs are postponed until they are
  closed; logins, bookmarks, passwords, game saves, chat history and personal folders are
  protected; logs and dumps from the last week and shader caches of games played in the last
  month are kept.
- Honest report: only really deleted bytes count as freed; busy and recent files and postponed
  categories are listed separately with the reason.
- Files are always deleted first, then empty folders; folders used by a running program are
  not taken apart; links to other places on the disk are never followed.
- Faster: the AI module loads on first use, services are read about 10× faster and changed via
  the Service Control Manager API instead of PowerShell.
- Python 3.14 (3.12+ supported), all dependencies updated; the `wmi` package replaced with
  direct `pywin32` calls.

### Fixed

- **New Gemini keys did not work:** the default model `gemini-2.5-flash` is now open only to
  earlier users, so every newly created key fell back to the mode without AI. The default is
  now `gemini-3.8-flash`; for Gemini 3 the temperature is left at Google's default and token
  limits include the model's thinking. `GEMINI_MODEL` still overrides the model.
- **A lost internet connection crashed the run** right after the restore point instead of
  continuing without AI: network errors from the Gemini SDK were not handled.
- The report after an AI failure no longer asks to enter a key that is already set; it says
  the AI did not respond and where to find the reason.
- The background “self-reflection” request sent full paths (with the Windows user name) to
  Gemini; it now sends folder names only, like the report.

## [1.0.4] — 2026-07-21

- Gemini key is entered in the app and stored encrypted (Windows DPAPI).
- Full offline mode: works without a key and falls back to built-in rules when the AI is
  unavailable.
- Critical services, system folders, drive roots and the user profile protected from changes.
- Faster junk scan, responsive UI during the scan, lighter background animation.
- Distributed as a single EXE.

## [1.0.3] — 2025-06-18

- Multiple user profiles (e.g. `Gamer` + `Developer`).
- Two-stage plan validation: system criticality, then relevance for the active profiles.
- Hybrid cleanup: standard cleanup plus AI-driven deep cleanup.
- WMI calls isolated in separate processes.
- `scripts/researcher.py` for generating knowledge-base rules.

## [1.0.2] — 2025-06-15

- Fixed loading of styles and icons in the packaged EXE.

## [1.0.1] — 2025-06-15

- Stability fixes for async tasks, progress bar rendering and incomplete AI responses.
- Lower CPU usage of the background animation.

## [1.0.0-beta] — 2025-06-14

- First public beta: one-click AI-assisted optimization, service and UWP app tuning,
  automatic restore point.

[1.1.0]: https://github.com/deeCaTofficial/WinSpectorPro/compare/v1.0.4...v1.1.0
[1.0.4]: https://github.com/deeCaTofficial/WinSpectorPro/releases/tag/v1.0.4
[1.0.3]: https://github.com/deeCaTofficial/WinSpectorPro/releases/tag/v1.0.3
[1.0.2]: https://github.com/deeCaTofficial/WinSpectorPro/releases/tag/v1.0.2
[1.0.1]: https://github.com/deeCaTofficial/WinSpectorPro/releases/tag/v1.0.1
[1.0.0-beta]: https://github.com/deeCaTofficial/WinSpectorPro/releases/tag/v1.0.0-beta
