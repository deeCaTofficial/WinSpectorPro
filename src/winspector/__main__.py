# src/winspector/__main__.py
"""
Обеспечивает возможность запуска пакета 'winspector' как исполняемого модуля
с помощью команды `python -m winspector`.

Эта точка входа не содержит собственной логики, а делегирует всю работу
основному лаунчеру 'src/main.py', чтобы избежать дублирования кода
и обеспечить консистентный запуск приложения.
"""

import runpy
import sys

# Проверяем, был ли файл запущен напрямую (что не рекомендуется).
# Конструкция `if __name__ == "__main__":` ниже обрабатывает основной случай.
if __name__ != "__main__":
    # Этот блок кода не должен выполняться при нормальном запуске.
    # Он здесь для полноты и как защита от неправильного импорта.
    print(
        "Ошибка: '__main__.py' не предназначен для прямого импорта.",
        file=sys.stderr
    )
    sys.exit(1)


# Используем runpy для запуска 'src/main.py' как главного скрипта.
# Это гарантирует, что вся логика настройки путей и проверок из 'main.py'
# будет выполнена точно так же, как при запуске `python src/main.py`.
#
# `run_path` выполняет код по указанному пути в новом модуле,
# эффективно имитируя прямой запуск этого файла.
try:
    # Предполагается, что `python -m winspector` запускается из корня проекта,
    # где находится папка `src`.
    runpy.run_path("src/main.py", run_name="__main__")
except FileNotFoundError:
    print(
        "Ошибка: Не удалось найти 'src/main.py'.\n"
        "Пожалуйста, запускайте приложение из корневой директории проекта, "
        "содержащей папку 'src'.",
        file=sys.stderr
    )
    sys.exit(1)
except Exception as e:
    # Отлавливаем любые другие неожиданные ошибки во время запуска
    print(f"Непредвиденная ошибка при запуске через '__main__.py': {e}", file=sys.stderr)
    sys.exit(1)