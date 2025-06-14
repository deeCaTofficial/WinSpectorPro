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
import re  # ### ИЗМЕНЕНИЕ: Импортируем модуль для регулярных выражений
from pathlib import Path

# --- ГЛАВНАЯ КОНФИГУРАЦИЯ СБОРКИ ---

# 1. Основные параметры приложения
APP_NAME = "WinSpectorPro"
ENTRY_POINT = "src/main.py"

# 2. Пути (рассчитываются автоматически)
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
ENTRY_POINT_PATH = PROJECT_ROOT / ENTRY_POINT
DIST_PATH = PROJECT_ROOT / "dist"
BUILD_PATH = PROJECT_ROOT / "build"
ICON_PATH = PROJECT_ROOT / "assets" / "app.ico"

# ### ИЗМЕНЕНИЕ: Более надежный способ получить версию
def get_project_version() -> str:
    """Читает версию из __init__.py с помощью регулярного выражения."""
    init_py_path = PROJECT_ROOT / "src" / "winspector" / "__init__.py"
    try:
        with open(init_py_path, "r", encoding="utf-8") as f:
            content = f.read()
        match = re.search(r"^__version__\s*=\s*['\"]([^'\"]*)['\"]", content, re.M)
        if match:
            return match.group(1)
        raise RuntimeError("Не удалось найти __version__ в файле.")
    except FileNotFoundError:
        print(f"❌ Ошибка: Не удалось найти {init_py_path} для определения версии.")
        sys.exit(1)
    except RuntimeError as e:
        print(f"❌ Ошибка: {e}")
        sys.exit(1)

APP_VERSION = get_project_version()

# 3. Файлы данных для включения в .exe
# Формат: ("относительный/путь/к/файлу", "путь/назначения/внутри/exe")
# Используем Path.as_posix() для универсальности путей
DATA_TO_INCLUDE = [
    (PROJECT_ROOT / "src/winspector/data/knowledge_base.yaml", "winspector/data"),
    (PROJECT_ROOT / "src/winspector/data/telemetry_domains.txt", "winspector/data"),
]

# 4. Продвинутые опции PyInstaller
# Скрытые импорты, которые PyInstaller может не найти автоматически
HIDDEN_IMPORTS = [
    "pygments",  # Часто нужен для форматирования вывода
    "google.generativeai.protos",
    "grpc._cython",  # ### ИЗМЕНЕНИЕ: Важно для google-generativeai
    "qasync",
    # ### ИЗМЕНЕНИЕ: Явное указание необходимых плагинов PyQt6
    "PyQt6.sip",
    "PyQt6.Qt6",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
    "PyQt6.QtCore",
]

# Модули, которые нужно исключить для уменьшения размера сборки
MODULES_TO_EXCLUDE = [
    "pytest",
    "PyQt5",
    "PySide6",
    "tkinter",
    "unittest",
    "pydoc",
    "pydoc_data",
]

# --- КОНЕЦ КОНФИГУРАЦИИ ---


def get_version_file_info():
    """Создает временный файл с информацией о версии для Windows."""
    version_file_content = f"""
# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({APP_VERSION.replace('.', ',')}, 0),
    prodvers=({APP_VERSION.replace('.', ',')}, 0),
    mask=0x3f,
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
    # ### ИЗМЕНЕНИЕ: Проверка, что скрипт запущен из корня проекта
    if not (PROJECT_ROOT / "src").exists() or not (PROJECT_ROOT / "scripts").exists():
        print("❌ Ошибка: Пожалуйста, запускайте этот скрипт из корневой директории проекта.")
        sys.exit(1)
        
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
        "--windowed", # Используем --windowed для GUI-приложения
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
    for src_path, dest_dir in DATA_TO_INCLUDE:
        if src_path.exists():
            # ### ИЗМЕНЕНИЕ: Используем os-специфичный разделитель
            command.extend(["--add-data", f"{src_path}{os.pathsep}{dest_dir}"])
        else:
            print(f"❌ Ошибка: файл данных не найден: {src_path}. Сборка прервана.")
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
    print("   " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in command))
    
    try:
        # ### ИЗМЕНЕНИЕ: Убираем capture_output, чтобы избежать проблем с кодировкой в Windows
        # Вывод PyInstaller будет отображаться в консоли в реальном времени.
        subprocess.run(command, check=True)
        print(f"\n✅ Сборка успешно завершена! ({DIST_PATH / (APP_NAME + '.exe')})")
    except subprocess.CalledProcessError as e:
        # Эта ошибка все еще может произойти, если PyInstaller вернет ненулевой код выхода
        print("\n❌ Ошибка сборки! PyInstaller завершился с ошибкой.")
        # Поскольку мы не захватывали вывод, он уже должен быть виден в консоли выше.
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