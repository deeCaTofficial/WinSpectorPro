# src/winspector/__main__.py
"""
Точка входа для запуска пакета как модуля: `python -m winspector`.

Собственной логики здесь нет — вся подготовка окружения (проверка ОС,
определение путей, режим сборки) живёт в `src/main.py`.
"""

import runpy
import sys
from pathlib import Path

# Путь вычисляется от расположения этого файла, а не от рабочей директории:
# запуск из любого каталога, кроме корня проекта, иначе завершался ошибкой
# «не удалось найти src/main.py».
LAUNCHER = Path(__file__).resolve().parents[1] / "main.py"


def main() -> None:
    """Передаёт управление основному лаунчеру."""
    if not LAUNCHER.is_file():
        print(f"Ошибка: не найден лаунчер {LAUNCHER}.", file=sys.stderr)
        sys.exit(1)

    # `main.py` импортирует пакет как `src.winspector`, поэтому в sys.path
    # должен быть корень репозитория, а не каталог `src`.
    project_root = LAUNCHER.parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    runpy.run_path(str(LAUNCHER), run_name="__main__")


if __name__ == "__main__":
    main()
