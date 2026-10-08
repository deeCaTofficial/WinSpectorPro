# WinSpector Pro v1.1.0 User Guide

**English** | [Русский](./USER_GUIDE_RU.md)

WinSpector Pro is an open-source Windows optimizer with AI-assisted personalization. This guide
explains what the program does and how to use it to make your system faster and cleaner.

> The interface is available in English and Russian. This guide uses the English names of
> buttons and screens.

## 💻 System requirements

-   **OS:** Windows 10 or Windows 11 (x64).
-   **Permissions:** administrator rights are required to apply optimizations.
-   **Internet:** needed only for the personalized plan from Google Gemini and for the update
    check. Without internet or a key, the program works from its built-in knowledge base.
    At startup it requests only the latest release information from GitHub (the version number);
    nothing about your system is sent without a Gemini key. Updates are never downloaded or
    installed automatically.
-   **Interface language:** Russian and English. The Windows display language is selected on first launch; your choice in Settings is saved.

## 🚀 Getting started

1.  **Download** `WinSpectorPro.exe` from the
    [Releases page](https://github.com/deeCaTofficial/WinSpectorPro/releases/latest). This is the
    whole program — nothing to install or unpack.
2.  **Run `WinSpectorPro.exe`.**
3.  The EXE is not code-signed yet, so Windows may show a SmartScreen warning. Click
    “More info” → “Run anyway”. To make sure the file has not been tampered with first,
    [verify its checksum](#-verify-the-download).
4.  The program asks for **administrator rights**. They are needed to create a restore point,
    configure services and clean system folders. Click “Yes”.
5.  The main screen opens immediately. To connect **Google Gemini**, click the gear in the
    window's title bar and open **Settings → Gemini**.
    The key is free and issued by
    [Google AI Studio](https://aistudio.google.com/apikey) (a Google account and age 18+ are
    required). The Gemini API is
    [not available in every country](https://ai.google.dev/gemini-api/docs/available-regions) —
    in particular, not in Russia or Belarus. Before saving, the program checks the key with one
    short request: if the key is wrong, the model is unavailable or your country is not
    supported, you will see it in the key dialog. Without a key, the program uses its
    built-in knowledge base.
6.  Click **Optimize** on the main screen — analysis and optimization start. A report
    opens when it is done.

## 🔐 Verify the download

The release notes list the SHA-256 of `WinSpectorPro.exe` (starting with v1.1.0 it is also
published next to the EXE as `WinSpectorPro.exe.sha256`). Compute the hash of your copy in
PowerShell and compare:

```powershell
Get-FileHash .\WinSpectorPro.exe -Algorithm SHA256
```

The hashes must match exactly. If the release notes contain a VirusTotal link, it points to the
scan of that exact file.

## ✨ The optimization process: what happens under the hood

WinSpector Pro is built around one button. Behind it are several steps.

1.  **Restore point.** Before any change, the program creates a Windows restore point. If it
    cannot be created, the run stops and nothing is changed. The one exception: Windows allows
    one restore point per day, and if a recent one already exists, the program continues —
    rolling back to it is still possible.
2.  **System scan.** The program collects technical information about your hardware and
    installed software.
3.  **Your profiles.** With AI connected, it determines a **set of roles**: you can be a gamer,
    a developer and a content creator at the same time. Without AI, the most cautious profile is
    used.
4.  **Junk and leftovers.** The program finds temporary files, caches and logs using its
    built-in knowledge base, and folders left behind by programs that were already uninstalled.
5.  **The plan.** The AI proposes a plan based on your profiles and the state of the system.
    The program's own code then checks the plan and drops anything unsafe. Without AI, the plan
    is built from the knowledge base and contains only actions that are safe for everyone.
6.  **Execution.** The program configures services and cleans the system. Files are deleted
    first, then leftovers of uninstalled programs are moved to quarantine, and emptied folders
    are removed last.
7.  **Report.** The result page shows:
    -   **totals** — how much space was actually freed, how many empty folders were removed and
        how many system changes were made;
    -   **Waiting for apps to close** — programs that were running, so their caches were left
        alone, and how much space they will free if you close them and run the optimization
        again;
    -   **System changes**, **Leftovers of uninstalled programs** (with a link to the
        quarantine) and **Gemini's notes**, or why the run was without AI;
    -   how many files were left alone because they were in use or too recent.

    **Copy report** copies the full text report; it is also saved to the reports folder
    (see **Settings → General**).

## 🧹 What is deleted and what never is

WinSpector Pro deletes only what programs **recreate on their own** or no longer need:

-   Windows and application temp files, the Windows Update cache, error reports;
-   browser and app caches (Chrome, Yandex, Firefox, Edge, Telegram, Discord and others);
-   program and game logs, crash dumps, shader and icon caches.

These rules cannot be overridden by settings or by the AI:

-   **Caches of running programs are left alone.** If a browser or messenger is open, its cache
    is cleaned the next time it is closed. The report lists the postponed categories.
-   **Recent data stays.** Temp files younger than a day, logs and dumps younger than a week, and
    shader caches of games you played in the last month are not deleted.
-   **Your data is protected.** Documents, downloads, pictures, videos, music, the desktop, game
    saves, chat history, and browser logins, bookmarks and passwords are not touched.

## 🗂️ Leftovers of uninstalled programs and quarantine

After programs are uninstalled, their folders with settings and logs often stay on the disk.
WinSpector Pro finds such folders only when there is clear evidence that the program is gone —
for example, its shortcut or uninstall entry points to files that no longer exist.

The same applies to:

-   **installer copies in `C:\Windows\Installer` and `Package Cache`** that no installed program
    needs. Office, drivers and development tools leave them after every update, and they add up
    to gigabytes over a year. This check runs only with administrator rights: without them
    Windows does not report the full list of installed products, and the program touches
    nothing;
-   **previous app versions** such as Discord and Figma that remain after auto-updates. An old
    version is moved only if the new one has been working for more than a week.

Leftovers are **not deleted right away** — they are moved to quarantine:

```
%LOCALAPPDATA%\WinSpectorPro\Quarantine\
```

They are kept there for **30 days** and deleted for good on the next run after that. If
something turns out to be missing, open **Settings → Quarantine**, select the leftovers and
click **Restore selected** — they are moved back to their original places. **Open folder**
shows the quarantine in Explorer; the `manifest.json` file in each batch lists the original
paths.

### Empty program folders

Uninstallers often delete all of a program's files but leave the folder itself — for example,
`C:\Program Files\Epic Games` with no games inside. WinSpector Pro removes such folders as the
last step, after files. A folder is removed only if it contains no files at all, and it is kept
if:

-   it was created or changed in the last week — something may be installing into it right now;
-   in `AppData` or `ProgramData`, its name matches an installed program;
-   it is part of Windows, or its permissions were set up specifically by an installer.

If you need the program again later, its installer will recreate the folder.

## 🛡️ How safety works

Optimizing a system is a reasonable thing to be cautious about, so protection has several
layers. No optimizer can guarantee absolute safety, but each layer can be checked in the open
source code.

-   **Restore points:** the main safety net. If something goes wrong, you can roll the system
    back to its state before the changes.
-   **Knowledge base:** built-in rules, including a list of critical system components. For the
    most important services and apps, the ban is hardcoded — neither the knowledge base nor the
    AI can change it.
-   **Plan validation:** the AI response is not trusted. A dedicated module checks every
    proposed action; if it could harm the system or one of your profiles (for example, disable
    the Steam service for a gamer), it is **rejected automatically**.
-   **The AI does not pick files:** the program itself builds the list of what may be deleted.
    The AI only answers whether to clean a whole category, so even a wrong model answer cannot
    lead to deleting an arbitrary file.
-   **Gemini key:** stored only on your computer, encrypted with Windows DPAPI. Only your user
    account on this computer can decrypt it. The key is sent only to Google, with requests to
    Gemini.
-   **Privacy:** only technical information is sent to the AI: hardware specs, names of
    installed programs and shortcuts, some environment variables (for example `PATH`, which may
    contain your account name), whether personal folders are empty, lists of services and apps,
    detected junk categories, and report totals with folder names (no full paths). The contents
    of your files and documents are not read or sent anywhere. On the free tier, Google may use
    the data it receives to improve its products, and human reviewers may read it
    ([Gemini API terms](https://ai.google.dev/gemini-api/terms)). Without a Gemini key, system
    analysis data is not sent. The app checks the latest release metadata on GitHub at startup.

## ❓ FAQ

**Q: Can I undo the changes?**
**A:** Yes. The most reliable way is the restore point created by WinSpector Pro. Open
Control Panel → Recovery → Open System Restore and pick the point described as
“WinSpector Pro - ” with the date of the run. Folders with leftovers of uninstalled programs can
be restored from quarantine (see above).

**Q: The program says it could not create a restore point. What now?**
**A:** The program tries to turn on System Protection for the system drive itself, but this can
be blocked by policy or by lack of space. Turn it on manually: Control Panel → System → System
Protection → select drive C: → Configure → Turn on system protection. Error details are in the
program log. Without a restore point the program deliberately changes nothing.

**Q: Does the program work without AI and without internet?**
**A:** Yes. Without a Gemini key the program cleans the system and applies cautious settings from
its built-in knowledge base: services are set to manual start instead of being disabled, and
built-in apps are not removed. If the AI is temporarily unavailable, the run automatically
continues without it.

**Q: Why does the program need administrator rights?**
**A:** Without them it is impossible to create a restore point, change the startup type of system
services, remove some preinstalled apps and clean system temp folders.

**Q: Why was less freed than found?**
**A:** Some files may be locked by running programs or be very recent — the program skips them on
purpose. Close browsers and messengers before optimizing to clean more.

**Q: Is it safe for my games / work software?**
**A:** The program determines your roles (gamer, developer, etc.) and rejects changes to
components that matter for any of them. Game saves are never deleted, and the shader cache of
games you play is kept, so games do not stutter after cleanup.

**Q: Why does the program sometimes suggest disabling a service it already disabled?**
**A:** Some Windows services (especially update- or telemetry-related ones) can turn themselves
back on after a reboot or an update. This is normal Windows behavior. WinSpector Pro detects it
and suggests optimizing them again.

**Q: Where is the program log?**
**A:** In the `logs` folder next to `WinSpectorPro.exe`, file `winspector.log`. Attach it to a
bug report — it speeds up the investigation a lot.

**Q: My antivirus complains about the program. What should I do?**
**A:** Optimizers interact with the system deeply, and antivirus products sometimes flag
PyInstaller-built EXEs heuristically. The EXE is not code-signed yet. To check the file: compare
its SHA-256 with the one in the release (see [Verify the download](#-verify-the-download)),
review the source code, or build the EXE yourself with `python scripts/build.py`. If you are sure
it is a false positive, report it to your antivirus vendor and in
[Issues](https://github.com/deeCaTofficial/WinSpectorPro/issues).
