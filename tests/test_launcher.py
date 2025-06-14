# tests/test_launcher.py
"""
Тесты для "лаунчера" приложения (src/main.py).

Эти тесты проверяют корректность обработки различных окружений
и условий запуска до того, как управление будет передано основному
модулю приложения.
"""

import pytest
import sys
import os
from unittest.mock import MagicMock, patch

# Импортируем тестируемые функции напрямую из src/main.py
# Это позволяет нам тестировать их изолированно.
from src.main import check_environment, run_app, emergency_log


class TestLauncherEnvironmentChecks:
    """Группа тестов для функции check_environment."""

    @patch('sys.platform', 'linux2')
    @patch('src.main._show_critical_error_message') # Мокаем показ GUI-сообщения
    def test_check_environment_exits_on_non_windows_os(self, mock_show_error, mocker):
        """Проверяет, что приложение завершается, если запущено не на Windows."""
        # GIVEN
        # Мокаем sys.exit, чтобы тест не прерывался
        mock_exit = mocker.patch('sys.exit')

        # WHEN
        check_environment()

        # THEN
        mock_show_error.assert_called_once()
        mock_exit.assert_called_once_with(1)

    @patch('sys.version_info', (3, 9, 5)) # Имитируем старый Python
    @patch('src.main._show_critical_error_message')
    def test_check_environment_exits_on_old_python_version(self, mock_show_error, mocker):
        """Проверяет, что приложение завершается, если версия Python слишком старая."""
        # GIVEN
        mock_exit = mocker.patch('sys.exit')
        
        # WHEN
        check_environment()

        # THEN
        mock_show_error.assert_called_once()
        mock_exit.assert_called_once_with(1)

    @patch('sys.platform', 'win32')
    @patch('sys.version_info', (3, 11, 0))
    def test_check_environment_passes_on_valid_system(self, mocker):
        """Проверяет, что проверка проходит на совместимой системе."""
        # GIVEN
        mock_exit = mocker.patch('sys.exit')

        # WHEN
        check_environment()

        # THEN
        mock_exit.assert_not_called()


class TestLauncherExecutionFlow:
    """Группа тестов для основной функции run_app."""

    @patch('src.main.check_environment')
    # ИСПРАВЛЕНИЕ: Мы мокаем 'app_main', который импортируется внутри run_app
    @patch('src.main.app_main')
    def test_run_app_in_dev_mode(self, mock_app_main, mock_check_env, monkeypatch, mocker):
        """Проверяет определение путей и вызов основной логики в режиме разработки."""
        # GIVEN
        monkeypatch.setattr(sys, 'frozen', False, raising=False)
        mocker.patch('sys.exit') # Мокаем sys.exit, чтобы тест не завершался
        
        # WHEN
        run_app()

        # THEN
        mock_check_env.assert_called_once()
        mock_app_main.assert_called_once()
        
        call_args = mock_app_main.call_args[0][0]
        assert isinstance(call_args, dict)
        # Путь к логам должен быть в корне проекта (на уровень выше 'src')
        assert str(call_args['logs']).endswith('logs')
        assert 'src' not in str(call_args['logs'])
        assert str(call_args['base']).endswith('src')

    @patch('src.main.check_environment')
    @patch('src.main.app_main')
    def test_run_app_in_frozen_mode(self, mock_app_main, mock_check_env, monkeypatch, mocker):
        """Проверяет определение путей и вызов основной логики в "замороженном" режиме (.exe)."""
        # GIVEN
        monkeypatch.setattr(sys, 'frozen', True)
        monkeypatch.setattr(sys, '_MEIPASS', 'C:\\Temp\\_MEI12345')
        monkeypatch.setattr(sys, 'executable', 'C:\\Program Files\\WinSpector\\WinSpectorPro.exe')
        mocker.patch('sys.exit')
        
        # WHEN
        run_app()

        # THEN
        mock_check_env.assert_called_once()
        mock_app_main.assert_called_once()
        
        call_args = mock_app_main.call_args[0][0]
        assert call_args['logs'] == 'C:\\Program Files\\WinSpector\\logs'
        assert call_args['base'] == 'C:\\Temp\\_MEI12345'


class TestEmergencyLogging:
    """Группа тестов для аварийного логирования."""

    def test_emergency_log_creates_dir_and_writes_file(self, mocker):
        """Проверяет, что аварийный логгер создает папку и записывает в файл."""
        # GIVEN
        # Мокаем Path, чтобы контролировать создание папок и файлов
        mock_path_obj = MagicMock()
        mock_path_class = mocker.patch('src.main.Path', return_value=mock_path_obj)
        mock_open = mocker.patch('builtins.open')
        
        error_message = "Test critical error"

        # WHEN
        emergency_log(error_message)

        # THEN
        # 1. Была вызвана .home(), чтобы найти домашнюю директорию
        mock_path_class.home.assert_called_once()
        # 2. Была предпринята попытка создать директорию
        # В коде используется `log_dir.mkdir(exist_ok=True)`, а не os.makedirs
        log_dir_mock = mock_path_class.home.return_value / ".winspector"
        log_dir_mock.mkdir.assert_called_once_with(exist_ok=True)
        # 3. Был открыт файл для записи
        log_file_mock = log_dir_mock / "winspector_crash.log"
        log_file_mock.open.assert_called_once_with("a", encoding="utf-8")
        
        # 4. В файл была произведена запись
        file_handle = log_file_mock.open.return_value.__enter__.return_value
        assert "CRASH AT" in file_handle.write.call_args_list[0][0][0]
        assert error_message in file_handle.write.call_args_list[1][0][0]

    @patch('src.main.check_environment', side_effect=ImportError("Critical import failed"))
    @patch('src.main.emergency_log')
    @patch('src.main._show_critical_error_message')
    def test_run_app_calls_emergency_log_on_critical_failure(self, mock_show_error, mock_emergency_log, mock_check_env, mocker):
        """Проверяет, что run_app вызывает аварийный лог при критическом сбое."""
        # GIVEN
        mock_exit = mocker.patch('sys.exit')
        
        # WHEN
        run_app()
        
        # THEN
        mock_check_env.assert_called_once() # Была вызвана проверка, которая вызвала ошибку
        mock_emergency_log.assert_called_once() # Был вызван аварийный логгер
        
        log_message = mock_emergency_log.call_args[0][0]
        assert "Критическая ошибка на этапе запуска" in log_message
        assert "Critical import failed" in log_message
        
        mock_show_error.assert_called_once() # Было показано сообщение об ошибке
        mock_exit.assert_called_once_with(1) # Приложение завершило работу