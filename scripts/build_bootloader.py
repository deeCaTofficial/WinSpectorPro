"""
Собирает загрузчик PyInstaller из исходников и ставит его в текущее окружение.

Зачем: стандартный загрузчик одинаков у всех программ на PyInstaller, в том
числе у вредоносных, поэтому часть антивирусов помечает любой EXE с ним
(например, «Dropper.Agent» или «W32.Malware.<хеш>» на VirusTotal). Загрузчик,
собранный у себя, отличается побайтно, и такие обобщённые сигнатуры на него
не срабатывают. Код загрузчика при этом тот же — из официального архива
PyInstaller с PyPI, хеш которого проверяется.

Нужны Microsoft C++ Build Tools (компонент «Разработка классических
приложений на C++»). Запускать один раз после установки или обновления
PyInstaller, затем как обычно: python scripts/build.py.

    .venv\\Scripts\\python scripts\\build_bootloader.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import PyInstaller

PYPI_URL = "https://pypi.org/pypi/pyinstaller/{version}/json"
PLATFORM_DIR = "Windows-64bit-intel"
LOADERS = ("run.exe", "runw.exe", "run_d.exe", "runw_d.exe")


def download_sdist(version: str, target: Path) -> Path:
    """Скачивает архив исходников с PyPI и сверяет его SHA-256 с опубликованным."""
    with urllib.request.urlopen(PYPI_URL.format(version=version)) as response:
        meta = json.load(response)
    sdist = next(item for item in meta["urls"] if item["packagetype"] == "sdist")
    with urllib.request.urlopen(sdist["url"]) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sdist["digests"]["sha256"]:
        raise SystemExit("SHA-256 архива PyInstaller не совпал с PyPI — сборка остановлена.")
    path = target / sdist["filename"]
    path.write_bytes(data)
    return path


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Скрипт собирает загрузчик для Windows.")
    version = PyInstaller.__version__
    installed = Path(PyInstaller.__file__).parent / "bootloader" / PLATFORM_DIR
    print(f"PyInstaller {version}: {installed}")

    with tempfile.TemporaryDirectory(prefix="pyinstaller-bootloader-") as tmp:
        work = Path(tmp)
        archive = download_sdist(version, work)
        print(f"Исходники: {archive.name} (SHA-256 совпал с PyPI)")
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        source = work / f"pyinstaller-{version}"
        # Новые файлы появятся только при успешной сборке: старые удаляем заранее.
        built_dir = source / "PyInstaller" / "bootloader" / PLATFORM_DIR
        for name in LOADERS:
            (built_dir / name).unlink(missing_ok=True)
        subprocess.run(
            [sys.executable, "./waf", "all", "--target-arch=64bit"],
            cwd=source / "bootloader",
            check=True,
        )
        for name in LOADERS:
            built = built_dir / name
            if not built.is_file():
                raise SystemExit(f"Загрузчик {name} не собран — см. вывод waf выше.")
            shutil.copy2(built, installed / name)
            digest = hashlib.sha256(built.read_bytes()).hexdigest()[:16]
            print(f"  {name}: {digest}…")
    print("Готово. Соберите EXE: python scripts/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
