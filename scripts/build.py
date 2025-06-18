# scripts/build.py
"""
Профессиональный скрипт для сборки WinSpector Pro с помощью PyInstaller.

Этот скрипт выполняет полный цикл сборки:
1. Компилирует ресурсы Qt (.qrc -> .py).
2. Динамически генерирует .spec файл из шаблона.
3. Запускает PyInstaller с сгенерированным .spec файлом.
4. Создает готовый к распространению ZIP-архив.
5. Поддерживает флаги для отладочной и релизной сборок.
"""
import os
import shutil
import subprocess
import sys
import re
import logging
from pathlib import Path
import argparse

# --- 1. НАСТРОЙКА ЛОГИРОВАНИЯ ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("build.log", mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

# --- 2. ГЛАВНАЯ КОНФИГУРАЦИЯ ---
APP_NAME = "WinSpectorPro"
ENTRY_POINT = "src/main.py"
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DIST_PATH = PROJECT_ROOT / "dist"
BUILD_PATH = PROJECT_ROOT / "build"
ICON_PATH = PROJECT_ROOT / "assets" / "app.ico"

def get_project_version() -> str:
    """Читает версию из __init__.py с помощью регулярного выражения."""
    init_py_path = PROJECT_ROOT / "src" / "winspector" / "__init__.py"
    try:
        content = init_py_path.read_text(encoding="utf-8")
        match = re.search(r"^__version__\s*=\s*['\"]([^'\"]*)['\"]", content, re.M)
        if match:
            return match.group(1)
        raise RuntimeError("Не удалось найти __version__ в файле.")
    except FileNotFoundError:
        logging.error(f"❌ Ошибка: Не удалось найти {init_py_path} для определения версии.")
        sys.exit(1)
    except RuntimeError as e:
        logging.error(f"❌ Ошибка: {e}")
        sys.exit(1)

APP_VERSION = get_project_version()

# Конфигурация для .spec файла
SPEC_CONFIG = {
    "datas": [
        ("src/winspector/data/knowledge_base", "winspector/data/knowledge_base"),
        ("src/winspector/resources/styles", "winspector/resources/styles"),
        ("assets", "assets"),
    ],
    "hiddenimports": [
        "pygments", "google.generativeai.protos", "grpc._cython", "qasync",
        "PyQt6.sip", "PyQt6.Qt6", "PyQt6.QtGui", "PyQt6.QtWidgets", "PyQt6.QtCore",
    ],
    "excludes": [
        "pytest", "PyQt5", "PySide6", "tkinter", "unittest", "pydoc", "pydoc_data",
    ]
}

# --- 3. ФУНКЦИИ-ПОМОЩНИКИ ---

def run_command(command: list, description: str):
    """Выполняет команду и логирует ее вывод, принудительно используя UTF-8."""
    logging.info(f"Начало: {description}...")
    try:
        # ### УЛУЧШЕНИЕ: Создаем копию переменных окружения и устанавливаем кодировку ###
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        # ### ИЗМЕНЕНИЕ: Убираем text=True и encoding, будем декодировать вручную ###
        process = subprocess.run(
            command, check=True, capture_output=True, env=env
        )
        
        # Декодируем вывод с игнорированием ошибок на всякий случай
        stdout = process.stdout.decode('utf-8', errors='ignore')
        stderr = process.stderr.decode('utf-8', errors='ignore')

        if stdout:
            logging.info(stdout)
        if stderr:
            logging.warning(stderr) # Логируем stderr как предупреждение

        logging.info(f"Успешно: {description}.")

    except subprocess.CalledProcessError as e:
        logging.error(f"❌ ОШИБКА: {description} завершился с ошибкой.")
        # Декодируем вывод из исключения тоже
        stdout = e.stdout.decode('utf-8', errors='ignore')
        stderr = e.stderr.decode('utf-8', errors='ignore')
        if stdout:
            logging.error(stdout)
        if stderr:
            logging.error(stderr)
        sys.exit(1)
    except FileNotFoundError:
        logging.error(f"❌ Ошибка: Команда '{command[0]}' не найдена. Убедитесь, что она установлена и доступна в PATH.")
        sys.exit(1)

def get_version_file_info() -> Path:
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
    version_file_path.write_text(version_file_content, encoding="utf-8")
    logging.info(f"📄 Информация о версии {APP_VERSION} создана.")
    return version_file_path

def generate_spec_from_template(is_debug: bool, version_file_path: Path) -> Path:
    """Динамически генерирует .spec файл из шаблона."""
    template_path = PROJECT_ROOT / "build.spec.template"
    spec_path = BUILD_PATH / f"{APP_NAME}.spec"
    logging.info(f"Генерация файла спецификации из шаблона: {template_path}")

    if not template_path.exists():
        logging.error(f"❌ Шаблон '{template_path}' не найден!")
        sys.exit(1)

    template_content = template_path.read_text(encoding="utf-8")

    datas_list = [
        f"('{str(PROJECT_ROOT / src).replace(os.sep, '/')}', '{dest}')"
        for src, dest in SPEC_CONFIG['datas']
    ]

    spec_content = template_content.format(
        entry_point=(PROJECT_ROOT / ENTRY_POINT).as_posix(),
        project_root=PROJECT_ROOT.as_posix(),
        datas=",".join(datas_list),
        hiddenimports=SPEC_CONFIG['hiddenimports'],
        excludes=SPEC_CONFIG['excludes'],
        app_name=APP_NAME,
        debug='True' if is_debug else 'False',
        console='True' if is_debug else 'False',
        icon_path=ICON_PATH.as_posix(),
        version_file_path=version_file_path.as_posix(),
    )

    spec_path.write_text(spec_content, encoding='utf-8')
    logging.info(f"Файл спецификации сохранен: {spec_path}")
    return spec_path

def create_distribution_archive():
    """Создает ZIP-архив из собранного приложения."""
    # ### ИСПРАВЛЕНИЕ: Правильно указываем пути для архивации ###
    
    # Имя папки, которую создал PyInstaller внутри 'dist'
    source_folder_name = APP_NAME 
    # Путь к этой папке
    source_path = DIST_PATH / source_folder_name
    
    # Имя для ZIP-архива без расширения
    archive_name = f"{APP_NAME}-v{APP_VERSION}"
    # Путь, где будет создан архив (на уровень выше, в самой папке dist)
    archive_path_base = DIST_PATH / archive_name

    logging.info(f"Создание архива: {archive_path_base}.zip")
    
    shutil.make_archive(
        base_name=str(archive_path_base),
        format='zip',
        root_dir=str(DIST_PATH), # Указываем, что "корень" для архивации - это папка dist
        base_dir=source_folder_name # Указываем, какую именно папку внутри root_dir нужно упаковать
    )
    logging.info("Архив успешно создан.")

def main():
    """Основная функция сборки."""
    parser = argparse.ArgumentParser(description="Скрипт сборки WinSpector Pro.")
    parser.add_argument("--debug", action="store_true", help="Собрать консольную версию для отладки.")
    parser.add_argument("--no-clean", action="store_true", help="Не удалять временные файлы после сборки.")
    parser.add_argument("--no-archive", action="store_true", help="Не создавать ZIP-архив после сборки.")
    args = parser.parse_args()

    build_type = "DEBUG" if args.debug else "RELEASE"
    logging.info(f"🚀 Начало сборки WinSpector Pro v{APP_VERSION} ({build_type})...")

    # 1. Очистка
    if DIST_PATH.exists(): shutil.rmtree(DIST_PATH)
    if BUILD_PATH.exists(): shutil.rmtree(BUILD_PATH)
    DIST_PATH.mkdir(exist_ok=True)
    BUILD_PATH.mkdir(exist_ok=True)

    # 2. Пред-сборочные шаги
    run_command([sys.executable, "scripts/compile_resources.py"], "Компиляция файлов ресурсов Qt")
    version_file = get_version_file_info()

    # 3. Генерация .spec файла
    spec_file = generate_spec_from_template(args.debug, version_file)

    # 4. Запуск PyInstaller
    run_command([sys.executable, "-m", "PyInstaller", str(spec_file), "--noconfirm"], 
                "Сборка приложения с PyInstaller")
    
    # 5. Пост-сборочные шаги
    if not args.no_archive:
        create_distribution_archive()

    # 6. Финальная очистка
    if not args.no_clean:
        logging.info("✨ Финальная очистка...")
        if BUILD_PATH.exists(): shutil.rmtree(BUILD_PATH)
        if spec_file.exists(): spec_file.unlink()
        logging.info(f"   - Временные файлы удалены.")

    logging.info("🏁 Готово!")

if __name__ == "__main__":
    main()