# scripts/compile_resources.py
import subprocess
import sys
from pathlib import Path

# --- КОНФИГУРАЦИЯ ---
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
RESOURCES_DIR = PROJECT_ROOT / "src" / "winspector" / "resources"
QRC_FILE = RESOURCES_DIR / "assets.qrc"
OUTPUT_FILE = RESOURCES_DIR / "assets_rc.py"

# Точный путь к компилятору, который мы нашли.
RCC_PATH = PROJECT_ROOT / "venv/Lib/site-packages/qt6_applications/Qt/bin/rcc.exe"

def main():
    """Компилирует .qrc и автоматически исправляет сгенерированный файл."""
    print("🚀 Компиляция файлов ресурсов Qt (.qrc)...")

    if not RCC_PATH.exists():
        print(f"❌ Критическая ошибка: Компилятор не найден по пути: {RCC_PATH}")
        sys.exit(1)
        
    if not QRC_FILE.exists():
        print(f"❌ Ошибка: Файл ресурсов не найден по пути: {QRC_FILE}")
        sys.exit(1)

    print(f"   - Используется компилятор: {RCC_PATH}")
    
    command = [ str(RCC_PATH), str(QRC_FILE), "-g", "python", "-o", str(OUTPUT_FILE) ]

    try:
        subprocess.run(command, check=True)
        print("✅ Компиляция ресурсов успешно завершена.")
        
        # --- Автоматическое исправление ---
        print("   - Автоматическое исправление импорта...")
        with open(OUTPUT_FILE, 'r+', encoding='utf-8', errors='ignore') as f:
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

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print("\n❌ КРИТИЧЕСКАЯ ОШИБКА КОМПИЛЯЦИИ РЕСУРСОВ")
        sys.exit(1)

if __name__ == "__main__":
    main()