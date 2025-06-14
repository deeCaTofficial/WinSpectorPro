# scripts/compile_resources.py
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
RESOURCES_DIR = PROJECT_ROOT / "src" / "winspector" / "resources"
QRC_FILES = [
    (RESOURCES_DIR / "assets.qrc", RESOURCES_DIR / "assets_rc.py")
]

def find_rcc_tool() -> Path:
    scripts_dir = Path(sys.executable).parent
    rcc_path = scripts_dir / "pyside6-rcc.exe"
    if not rcc_path.exists():
        raise FileNotFoundError(
            f"Не удалось найти 'pyside6-rcc.exe' по пути: {rcc_path}\n"
            f"Пожалуйста, убедитесь, что 'pyside6' установлен: pip install pyside6"
        )
    return rcc_path

def compile_resources():
    print("🚀 Компиляция файлов ресурсов Qt (.qrc) с помощью pyside6-rcc...")
    try:
        rcc_path = find_rcc_tool()
        print(f"   - Используется утилита: {rcc_path}")
    except FileNotFoundError as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)

    for qrc_file, output_file in QRC_FILES:
        if not qrc_file.exists():
            print(f"⚠️  Пропуск: Файл ресурсов не найден: {qrc_file}")
            continue
        print(f"   - Компиляция: {qrc_file.name} -> {output_file.name}")
        
        # --- ГЛАВНОЕ ИЗМЕНЕНИЕ ---
        # Мы формируем команду с абсолютными путями
        command = [
            str(rcc_path),
            str(qrc_file.resolve()), # Абсолютный путь к QRC
            "-o",
            str(output_file.resolve()) # Абсолютный путь к PY
        ]
        
        try:
            # Запускаем из корневой папки проекта
            subprocess.run(command, check=True, shell=True, cwd=PROJECT_ROOT)
        except subprocess.CalledProcessError as e:
            print(f"❌ Ошибка при компиляции {qrc_file.name}:")
            print(f"   Команда завершилась с кодом ошибки: {e.returncode}")
            sys.exit(1)
            
    print("✅ Компиляция ресурсов завершена.")

if __name__ == "__main__":
    compile_resources()