# scripts/build.py
"""
Скрипт для сборки WinSpector Pro с помощью PyInstaller.

Этот скрипт автоматизирует процесс сборки, обеспечивая консистентность
и упрощая создание исполняемого файла. Он корректно включает все
необходимые файлы данных, "вшивает" информацию о версии и позволяет
гибко настраивать процесс сборки.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- ГЛАВНАЯ КОНФИГУРАЦИЯ СБОРКИ ---

# 1. Основные параметры приложения
APP_NAME = "WinSpectorPro"
ENTRY_POINT = "src/main.py"

# Динамически получаем версию из __init__.py, чтобы не дублировать ее
# Это более надежный подход, чем ручное обновление версии здесь
try:
    with open("src/winspector/__init__.py", "r") as f:
        for line in f:
            if line.startswith("__version__"):
                # __version__ = "1.0.0" -> "1.0.0"
                APP_VERSION = line.split("=")[1].strip().strip('"').strip("'")
                break
except FileNotFoundError:
    print("❌ Ошибка: Не удалось найти src/winspector/__init__.py для определения версии. Сборка прервана.")
    sys.exit(1)


# 2. Пути (рассчитываются автоматически)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
ENTRY_POINT_PATH = PROJECT_ROOT / ENTRY_POINT
DIST_PATH = PROJECT_ROOT / "dist"
BUILD_PATH = PROJECT_ROOT / "build"
ICON_PATH = PROJECT_ROOT / "assets" / "app.ico"

# 3. Файлы данных для включения в .exe
# Формат: ("относительный/путь/к/файлу", "путь/назначения/внутри/exe")
DATA_TO_INCLUDE = [
    ("src/winspector/data/knowledge_base.yaml", "winspector/data"),
    ("src/winspector/data/telemetry_domains.txt", "winspector/data"),
]

# 4. Продвинутые опции PyInstaller
# Скрытые импорты, которые PyInstaller может не найти автоматически
HIDDEN_IMPORTS = [
    "pygments.styles.default", # Пример, может понадобиться для rich/logging
    "google.generativeai.protos",
]

# Модули, которые нужно исключить для уменьшения размера сборки
MODULES_TO_EXCLUDE = [
    "pytest",
    "PyQt5", # Исключаем на всякий случай, если он есть в окружении
    "tkinter",
    "unittest",
]

# --- КОНЕЦ КОНФИГУРАЦИИ ---


def get_version_file_info():
    """Создает временный файл с информацией о версии для Windows."""
    version_file_content = f"""
# UTF-8
#
# Для получения дополнительной информации о VS_VERSION_INFO см.
# http://msdn.microsoft.com/en-us/library/ms646997.aspx

VSVersionInfo(
  ffi=FixedFileInfo(
    # filevers и prodvers являются 4-х частными кортежами: (_major, _minor, _patch, _build)
    filevers=({APP_VERSION.replace('.', ',')}, 0),
    prodvers=({APP_VERSION.replace('.', ',')}, 0),
    # Установите FileFlags в 0x3f, если это отладочная сборка
    mask=0x3f,
    # Установите FileFlags в 0x0, если это релизная сборка
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'CLC corporation'),
        StringStruct(u'FileDescription', u'WinSpector Pro - AI-Powered Windows Optimizer'),
        StringStruct(u'FileVersion', u'{APP_VERSION}'),
        StringStruct(u'InternalName', u'{APP_NAME}'),
        StringStruct(u'LegalCopyright', u'© CLC corporation. All rights reserved.'),
        StringStruct(u'OriginalFilename', u'{APP_NAME}.exe'),
        StringStruct(u'ProductName', u'WinSpector Pro'),
        StringStruct(u'ProductVersion', u'{APP_VERSION}')])
      ]), 
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""
    version_file_path = BUILD_PATH / "version_info.txt"
    with open(version_file_path, "w", encoding="utf-8") as f:
        f.write(version_file_content)
    print(f"📄 Информация о версии {APP_VERSION} создана.")
    return version_file_path


def main():
    """Основная функция сборки."""
    print(f"🚀 Начало сборки WinSpector Pro v{APP_VERSION}...")

    # 1. Очистка и подготовка
    print("🧹 Очистка старых артефактов сборки...")
    if DIST_PATH.exists(): shutil.rmtree(DIST_PATH)
    if BUILD_PATH.exists(): shutil.rmtree(BUILD_PATH)
    BUILD_PATH.mkdir(exist_ok=True)

    # 2. Создание файла с информацией о версии
    version_file = get_version_file_info()

    # 3. Формирование команды PyInstaller
    command = [
        sys.executable,
        "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        f"--distpath={DIST_PATH}",
        f"--workpath={BUILD_PATH}",
        f"--version-file={version_file}",
    ]

    # Добавление иконки
    if ICON_PATH.exists():
        command.append(f"--icon={ICON_PATH}")
    else:
        print(f"⚠️ Иконка не найдена по пути: {ICON_PATH}. Сборка без иконки.")

    # Добавление файлов данных
    for src_rel, dest_rel in DATA_TO_INCLUDE:
        src_abs = PROJECT_ROOT / src_rel
        if src_abs.exists():
            command.extend(["--add-data", f"{src_abs}{os.pathsep}{dest_rel}"])
        else:
            print(f"❌ Ошибка: файл данных не найден: {src_abs}. Сборка прервана.")
            sys.exit(1)

    # Добавление скрытых импортов
    for module in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", module])
        
    # Исключение ненужных модулей
    for module in MODULES_TO_EXCLUDE:
        command.extend(["--exclude-module", module])

    # Добавление точки входа
    command.append(str(ENTRY_POINT_PATH))
    
    # 4. Запуск PyInstaller
    print("\n⚙️ Запуск PyInstaller...")
    print("   " + " ".join(f'"{c}"' if " " in c else c for c in command))
    
    try:
        subprocess.run(command, check=True, text=True, capture_output=False, encoding='utf-8')
        print(f"\n✅ Сборка успешно завершена! ({DIST_PATH / (APP_NAME + '.exe')})")
    except subprocess.CalledProcessError as e:
        print("\n❌ Ошибка сборки! PyInstaller завершился с ошибкой.")
        sys.exit(1)
    except FileNotFoundError:
        print("\n❌ Ошибка: PyInstaller не найден. Установите его: pip install pyinstaller")
        sys.exit(1)
    
    # 5. Финальная очистка
    print("\n✨ Финальная очистка...")
    try:
        shutil.rmtree(BUILD_PATH)
        print(f"   - Временная папка '{BUILD_PATH}' удалена.")
    except OSError as e:
        print(f"   ⚠️ Не удалось удалить временную папку '{BUILD_PATH}': {e}")

    print("\n🏁 Готово!")


if __name__ == "__main__":
    main()