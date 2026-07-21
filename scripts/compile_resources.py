# scripts/compile_resources.py
import shutil
import subprocess
import sys
from pathlib import Path

# --- КОНФИГУРАЦИЯ ---
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
RESOURCES_DIR = PROJECT_ROOT / "src" / "winspector" / "resources"
QRC_FILE = RESOURCES_DIR / "assets.qrc"
OUTPUT_FILE = RESOURCES_DIR / "assets_rc.py"

for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def find_rcc() -> Path | None:
    """Находит компилятор ресурсов в активном окружении или PATH."""
    executable_dir = Path(sys.executable).parent
    candidates = [
        executable_dir / "pyrcc6.exe",
        executable_dir / "pyside6-rcc.exe",
        shutil.which("pyrcc6"),
        shutil.which("pyside6-rcc"),
        shutil.which("rcc"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    return None


def main():
    """Компилирует .qrc и автоматически исправляет сгенерированный файл."""
    print("🚀 Компиляция файлов ресурсов Qt (.qrc)...")

    if not QRC_FILE.exists():
        print(f"❌ Ошибка: Файл ресурсов не найден по пути: {QRC_FILE}")
        sys.exit(1)

    rcc_path = find_rcc()
    if rcc_path is None:
        if OUTPUT_FILE.is_file() and OUTPUT_FILE.stat().st_mtime >= QRC_FILE.stat().st_mtime:
            print("⚠️ Компилятор Qt не найден; используется актуальный assets_rc.py.")
            return
        print("❌ Компилятор ресурсов не найден (pyrcc6, pyside6-rcc или rcc).")
        sys.exit(1)

    print(f"   - Используется компилятор: {rcc_path}")

    if "pyrcc" in rcc_path.name.lower():
        command = [str(rcc_path), str(QRC_FILE), "-o", str(OUTPUT_FILE)]
    else:
        command = [str(rcc_path), str(QRC_FILE), "-g", "python", "-o", str(OUTPUT_FILE)]

    try:
        subprocess.run(command, check=True)
        print("✅ Компиляция ресурсов успешно завершена.")

        # --- Автоматическое исправление ---
        print("   - Автоматическое исправление импорта...")
        with OUTPUT_FILE.open("r+", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            # Заменяем импорт PySide6 на PyQt6
            new_content = content.replace("from PySide6 import QtCore", "from PyQt6 import QtCore")
            if new_content != content:
                f.seek(0)
                f.write(new_content)
                f.truncate()
                print("   - ✅ Импорт исправлен на PyQt6.")
            else:
                print("   - ✅ Исправление не потребовалось, импорт уже корректен.")

    except (subprocess.CalledProcessError, FileNotFoundError):
        print("\n❌ КРИТИЧЕСКАЯ ОШИБКА КОМПИЛЯЦИИ РЕСУРСОВ")
        sys.exit(1)


if __name__ == "__main__":
    main()
