<p align="center">
  <img src="https://img.shields.io/badge/-English-1f6feb?style=for-the-badge" alt="English">
  <a href="./CONTRIBUTING_RU.md"><img src="https://img.shields.io/badge/-%D0%A0%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9-30363d?style=for-the-badge" alt="Русский"></a>
</p>

# Contributing to WinSpector Pro

Thank you for wanting to help! Every contribution is welcome: a bug report, an idea, a typo fix,
or new code. This guide walks you through setting up the environment and sending your first
pull request. Issues and pull requests can be written in English or Russian.

## Contents

- [Code of Conduct](#code-of-conduct)
- [Ways to contribute](#ways-to-contribute)
- [Reporting security issues](#reporting-security-issues)
- [Setting up the environment](#setting-up-the-environment)
- [Dependencies](#dependencies)
- [Code and pull requests](#code-and-pull-requests)
- [Interface text in two languages](#interface-text-in-two-languages)
- [Cleanup rules](#cleanup-rules)
- [Growing the knowledge base with researcher.py](#growing-the-knowledge-base-with-researcherpy)
- [Building and releasing](#building-and-releasing)

## Code of Conduct

By taking part in this project you agree to follow its [Code of Conduct](./CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug:** [open an issue](https://github.com/deeCaTofficial/WinSpectorPro/issues/new/choose)
  using the "🐞 Сообщение об ошибке" (bug report) template.
- **Suggest an idea:** [open an issue](https://github.com/deeCaTofficial/WinSpectorPro/issues/new/choose)
  using the "✨ Предложение функции" (feature request) template.
- **Improve the docs:** spotted a typo or an unclear passage? Propose a fix.
- **Write code:** fix a bug or implement a feature. For larger changes, open an issue to discuss
  the idea first, so you don't spend time on something that won't be merged.

## Reporting security issues

The app runs with administrator rights, so please don't report vulnerabilities in public
issues. Report them privately instead: **Security** tab → **Report a vulnerability**.

## Setting up the environment

> **Requirements:** Windows 10/11 and Python **3.12 or newer** (3.14 is the main development
> version). The app runs on Windows only: it works with the registry, services, and WMI.

1. **Fork and clone.** Fork the repository and clone **your fork**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/WinSpectorPro.git
   cd WinSpectorPro
   ```

2. **Virtual environment:**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Dependencies.** `requirements-dev.txt` includes everything from `requirements.txt` plus the
   tools for development, testing, and building:
   ```bash
   pip install -r requirements-dev.txt
   ```

4. **Gemini key (optional).** Without a key the app runs in its no-AI mode, which is enough for
   most work. You only need a key to change or test the AI mode. Copy `.env.example` to `.env`
   and add your key: `GEMINI_API_KEY="YOUR_KEY"`. `.env` is already in `.gitignore` and won't be
   committed.

5. **Check that it works.** Run the app and the tests:
   ```bash
   python src/main.py
   pytest
   ```
   Tests marked `windows` read the real system (WMI, registry) and never change it. For a quick
   run without them: `pytest -m "not slow and not windows"`.

6. **Pre-commit checks.** Install the pre-commit hooks — they run `ruff check`, `ruff format`,
   the fast tests, and strip trailing whitespace:
   ```bash
   pre-commit install
   ```

The app's internals are described in [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md).

## Dependencies

Don't edit `requirements.txt` or `requirements-dev.txt` by hand: they are generated.

To add, update, or remove a dependency:

1. Install `pip-tools` if you don't have it: `pip install pip-tools`.
2. Edit `requirements.in` (for the app) or `requirements-dev.in` (for development).
3. Regenerate **both** files — `pip-compile` only updates the `.txt` named in the command:
   ```bash
   pip-compile --no-index --strip-extras --output-file=requirements.txt requirements.in
   pip-compile --no-index --strip-extras --output-file=requirements-dev.txt requirements-dev.in
   ```
   Add `--upgrade` to bump all packages to their latest versions.
4. Commit all four files (`.in` and `.txt`).

## Code and pull requests

### 1. Branch

Create a separate branch from `main` for each task, named `type/short-description`.
Types: `feature/`, `fix/`, `docs/`, `refactor/`, `test/`.

```bash
git checkout -b fix/crash-on-startup
```

### 2. Code and commits

- **Code style.** The project uses `ruff` for linting and formatting:
  ```bash
  ruff check .
  ruff format .
  ```
- **Tests.** Cover new behavior and bug fixes with tests in `tests/`.
- **Commit messages** follow [Conventional Commits](https://www.conventionalcommits.org/): this
  keeps the history readable and makes `CHANGELOG.md` (maintained by hand) easier to write.
  Examples: `feat(gui): add preview window`, `fix(core): prevent race condition in analyzer`.

### 3. Pull request

Push the branch to your fork and open a pull request against the main repository.

- **Title** — short and to the point.
- **Description** — **what** changed and **why**. If the PR closes an issue, say so:
  `Closes #123`.
- **CI checks.** GitHub Actions runs `ruff check`, `ruff format --check`, and the tests on
  Python 3.12, 3.13, and 3.14 for every PR. A PR is merged once all checks are green.
- **Documentation.** If the change is visible to users, add a line to the `Unreleased` section
  of [`CHANGELOG.md`](./CHANGELOG.md). The README and the user guide exist in two languages —
  update both versions (`README.md` and `README_RU.md`, `docs/USER_GUIDE.md` and
  `docs/USER_GUIDE_RU.md`). If the look of a window changed, update the screenshots as described
  in [`docs/media/README.md`](./docs/media/README.md).

## Interface text in two languages

The interface is available in Russian and English. Pass every user-visible string in both
languages at once: in windows use `self._t("Русский текст", "English text")`, elsewhere use
`localize(language, russian, english)` from `winspector/gui/language.py`. A string in only one
language shows up untranslated for users of the other.

## Cleanup rules

Cleanup rules live in `src/winspector/data/knowledge_base/cleanup_rules.yaml`. A mistake here
costs users their data, so these rules have strict requirements:

1. **Only what can be recreated.** A rule targets caches, logs, temporary files, or dumps —
   things the program rebuilds on its own. Settings, saves, databases, chat history, and login
   sessions do not belong here.
2. **Never a whole Chromium/CEF profile.** Folders like `CefCache`, `EBWebView`, or
   `BrowserCache` often turn out to be a full profile with cookies and a signed-in account.
   Target only the cache folders inside: `Cache`, `Code Cache`, `GPUCache`, `GrShaderCache`,
   `component_crx_cache`.
3. **Check the contents.** Before adding a rule, look at what the folder actually contains, and
   attach the output of a dry run to the PR — it deletes nothing:
   ```bash
   python scripts/audit_cleanup.py --out audit.md
   ```
4. **Pick `safety`.** `high` is cleaned automatically, including without AI; `medium` only when
   the AI approves it. If recovery is expensive (re-downloading gigabytes, re-indexing a project)
   or unconfirmed, use `medium`.
5. **Use the constraints:**
   - `min_age_hours` — leave recent files alone (logs: 168, shader caches: 720);
   - `requires_closed` — postpone the category while a program is running (`["chrome.exe"]`);
   - `skip_if_busy` and `busy_siblings` — for apps whose process name isn't known in advance;
   - `atomic_subdirs` — for temp folders: a subfolder in use is left untouched as a whole;
   - `*` in a path stands for a profile or a game (`User Data\*\Cache`) but can't be the target
     itself.

`tests/test_knowledge_base.py` checks the rules automatically: the target of a `high` rule must
look like a cache or a log.

## Growing the knowledge base with researcher.py

The app's rules are stored in the knowledge base (`src/winspector/data/knowledge_base/`). The
internal tool `scripts/researcher.py` helps draft new rules: it collects information about the
system, asks Gemini to propose optimization and cleanup rules based on it, and then re-checks
every proposal with a separate request. It needs `GEMINI_API_KEY` in `.env`; it asks for
administrator rights on its own.

> [!WARNING]
> `researcher.py` sends information about your system to Google Gemini: the lists of services,
> startup items, and scheduled tasks, the hosts file, current network connections with process
> names, and installed programs with their install paths. If you'd rather not share this, run it
> in a virtual machine.

How to help:

1. Run the script on your machine or in a virtual machine with an interesting set of programs:
   ```bash
   python scripts/researcher.py
   ```
2. Wait for it to finish — this can take a long time.
3. Review the generated YAML files in `tests/upload/`.
4. Pick high-quality, broadly useful rules and propose them in a pull request. Check AI-suggested
   cleanup rules against the [Cleanup rules](#cleanup-rules) requirements: earlier versions of the
   knowledge base included, for example, the Downloads folder and Defender's quarantine.

Rule descriptions in the knowledge base are written in Russian (`description_ru`).

## Building and releasing

You can build the EXE in the same environment:

```bash
python scripts/build.py
```

The output is `dist/WinSpectorPro.exe` and its SHA-256 in `dist/WinSpectorPro.exe.sha256`.
Flags: `--debug` builds a console version for debugging, `--archive` also creates a ZIP with the
EXE (not used for releases), `--no-clean` keeps temporary build files.

Releasing a version (for maintainers):

1. Bump the version in `src/winspector/__init__.py` (`__version__`, read by the build) and in
   `pyproject.toml`.
2. In `CHANGELOG.md`, replace `Unreleased` with the date and add a version comparison link.
3. Make sure `pytest` and `ruff check .` pass. If PyInstaller was installed or updated, build your
   own bootloader: `python scripts/build_bootloader.py` (requires Microsoft C++ Build Tools).
   Some antivirus engines flag PyInstaller's stock bootloader in any program built with it;
   a locally built one doesn't match those generic signatures. Then build the EXE:
   `python scripts/build.py`.
4. Prepare the release notes from the draft in `docs/releases/` (for example,
   [`docs/releases/v1.1.0.md`](./docs/releases/v1.1.0.md)) and insert the hash from
   `dist/WinSpectorPro.exe.sha256`.
5. Optionally upload the EXE to [VirusTotal](https://www.virustotal.com/) and add a link to the
   report. If you didn't, delete the VirusTotal line instead of leaving a placeholder.
6. Create a release tagged `vX.Y.Z` and attach **both** files: `WinSpectorPro.exe` and
   `WinSpectorPro.exe.sha256`.

The EXE is not digitally signed. Until it is, don't claim otherwise in release notes or docs.
