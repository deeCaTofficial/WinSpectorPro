# tests/test_application_integration.py
"""
Интеграционные тесты для проверки основного потока запуска приложения.

Эти тесты проверяют, что все ключевые компоненты (GUI, ядро, логгер)
корректно инициализируются и взаимодействуют друг с другом при запуске.
"""

import pytest
import sys
from unittest.mock import MagicMock, patch
from PyQt6.QtWidgets import QMessageBox

# Импортируем тестируемую функцию один раз в начале файла
from src.winspector.application import main as app_main


@pytest.fixture
def mock_app_environment(mocker):
    """
    Фикстура, которая мокает все зависимости, необходимые для запуска app_main.
    Это позволяет нам тестировать логику запуска, не создавая реальные окна или ядро.
    """
    # Мокаем QApplication и все, что с ним связано
    mock_qapplication = mocker.patch('src.winspector.application.QApplication')
    mock_app_instance = mock_qapplication.return_value
    mock_app_instance.exec.return_value = 0  # Имитируем успешный выход

    # Мокаем MainWindow
    mock_main_window = mocker.patch('src.winspector.application.MainWindow')
    
    # Мокаем ядро
    mock_core = mocker.patch('src.winspector.application.WinSpectorCore')

    # Мокаем функции-помощники
    mock_is_admin = mocker.patch('src.winspector.application.check_admin_rights', return_value=True)
    mock_relaunch = mocker.patch('src.winspector.application.relaunch_as_admin')
    
    # Мокаем QSharedMemory для контроля над проверкой "уже запущено"
    mock_shared_memory = mocker.patch('src.winspector.application.QSharedMemory')
    # По умолчанию, create() возвращает True (приложение не запущено)
    mock_shared_memory.return_value.create.return_value = True

    # Мокаем QMessageBox для проверки диалоговых окон
    mock_qmessagebox = mocker.patch('src.winspector.application.QMessageBox')

    # Мокаем настройку логирования, чтобы не создавать реальные файлы
    mocker.patch('src.winspector.application.setup_logging')

    # Возвращаем словарь с моками для удобного доступа в тестах
    return {
        "QApplication": mock_qapplication,
        "app_instance": mock_app_instance,
        "MainWindow": mock_main_window,
        "WinSpectorCore": mock_core,
        "is_admin": mock_is_admin,
        "relaunch_as_admin": mock_relaunch,
        "QSharedMemory": mock_shared_memory,
        "QMessageBox": mock_qmessagebox,
    }


# --- Тесты для различных сценариев запуска ---

def test_main_successful_launch_with_admin_rights(mock_app_environment):
    """
    Тест "счастливого пути": приложение запускается с правами администратора.
    """
    # GIVEN: Права администратора есть (по умолчанию в фикстуре)
    # и приложение еще не запущено (по умолчанию в фикстуре)
    
    # Создаем фейковый словарь путей, как это делает src/main.py
    fake_paths = {"logs": "path/to/logs", "base": "path/to/base"}

    # WHEN: Запускаем основную функцию приложения
    result = app_main(fake_paths)

    # THEN: Проверяем, что все ключевые компоненты были вызваны корректно
    mock_app_environment["WinSpectorCore"].assert_called_once()
    mock_app_environment["MainWindow"].assert_called_once()
    mock_app_environment["MainWindow"].return_value.show.assert_called_once()
    mock_app_environment["app_instance"].exec.assert_called_once()
    
    # Убедимся, что перезапуск не вызывался
    mock_app_environment["relaunch_as_admin"].assert_not_called()
    assert result == 0


def test_main_relaunch_when_no_admin_rights(mock_app_environment):
    """
    Тест сценария, когда приложение запущено без прав администратора.
    Оно должно предложить перезапуск и завершиться.
    """
    # GIVEN: Прав администратора нет
    mock_app_environment["is_admin"].return_value = False
    
    # Имитируем нажатие "Да" в диалоговом окне
    mock_msg_box_instance = mock_app_environment["QMessageBox"]
    mock_msg_box_instance.question.return_value = QMessageBox.StandardButton.Yes

    fake_paths = {"logs": "path/to/logs", "base": "path/to/base"}

    # WHEN
    result = app_main(fake_paths)

    # THEN
    # 1. Диалоговое окно было показано
    mock_app_environment["QMessageBox"].question.assert_called_once()
    
    # 2. Была вызвана функция перезапуска
    mock_app_environment["relaunch_as_admin"].assert_called_once()
    
    # 3. Ядро и главное окно НЕ были созданы
    mock_app_environment["WinSpectorCore"].assert_not_called()
    mock_app_environment["MainWindow"].assert_not_called()
    
    assert result == 0


def test_main_exits_if_already_running(mock_app_environment):
    """
    Тест сценария, когда пользователь пытается запустить вторую копию приложения.
    """
    # GIVEN: Права администратора есть, но QSharedMemory говорит, что копия уже запущена
    mock_app_environment["QSharedMemory"].return_value.create.return_value = False

    fake_paths = {"logs": "path/to/logs", "base": "path/to/base"}
    
    # WHEN
    result = app_main(fake_paths)

    # THEN
    # 1. Было показано окно с предупреждением
    mock_app_environment["QMessageBox"].warning.assert_called_once()
    
    # 2. Ядро и главное окно НЕ были созданы
    mock_app_environment["WinSpectorCore"].assert_not_called()
    mock_app_environment["MainWindow"].assert_not_called()
    
    assert result == 0