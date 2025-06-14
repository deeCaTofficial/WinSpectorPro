# tests/core/test_windows_optimizer_execution.py
"""
Тесты для логики выполнения плана оптимизации в WindowsOptimizer.

Основное внимание уделяется корректной генерации PowerShell-скриптов
на основе входного плана от ИИ.
"""
import pytest
import asyncio
from unittest.mock import MagicMock, call

from src.winspector.core.modules.windows_optimizer import WindowsOptimizer

# Помечаем все тесты в этом файле как асинхронные
pytestmark = pytest.mark.asyncio


@pytest.fixture
def optimizer() -> WindowsOptimizer:
    """Простая фикстура для создания экземпляра WindowsOptimizer."""
    return WindowsOptimizer()


@pytest.fixture
def mock_powershell_runner(mocker) -> MagicMock:
    """
    Фикстура, которая мокает внутренний метод _run_powershell_script,
    чтобы предотвратить реальное выполнение команд. Возвращает мок
    и итоговый отчет.
    """
    # Создаем мок, который будет возвращать стандартный успешный отчет
    summary = {"disabled_services": [], "removed_apps": [], "errors": []}
    mock_runner = mocker.patch(
        'src.winspector.core.modules.windows_optimizer.WindowsOptimizer._run_powershell_script',
        return_value=summary
    )
    return mock_runner


class TestActionPlanExecution:
    """Группа тестов для метода execute_action_plan."""

    async def test_disables_service_correctly(self, optimizer: WindowsOptimizer, mock_powershell_runner: MagicMock):
        """Проверяет, что для отключения службы генерируется правильная команда."""
        # GIVEN
        action_plan = [{
            "type": "service",
            "id": "Spooler",
            "action": "disable",
            "reason": "Print spooler, not needed"
        }]

        # WHEN
        # Мы вызываем основной метод, но его внутренняя часть (_run_powershell_script) замокана
        await optimizer.execute_action_plan(action_plan, lambda p, t: None)

        # THEN
        # Проверяем, что наш мок был вызван с правильно сгенерированным скриптом
        mock_powershell_runner.assert_called_once()
        # Получаем аргументы вызова (script_content)
        script_arg = mock_powershell_runner.call_args[0][0]
        
        assert "Stop-Service -Name 'Spooler' -Force" in script_arg
        assert "Set-Service -Name 'Spooler' -StartupType Disabled" in script_arg

    async def test_removes_uwp_app_using_package_full_name(self, optimizer: WindowsOptimizer, mock_powershell_runner: MagicMock):
        """Проверяет, что UWP-приложение удаляется по полному имени пакета, если оно есть."""
        # GIVEN
        action_plan = [{
            "type": "uwp_app",
            "id": "Microsoft.YourPhone",
            "package_full_name": "Microsoft.YourPhone_1.2.3.4_x64__8wekyb3d8bbwe",
            "action": "remove",
            "reason": "Bloatware"
        }]

        # WHEN
        await optimizer.execute_action_plan(action_plan, lambda p, t: None)

        # THEN
        mock_powershell_runner.assert_called_once()
        script_arg = mock_powershell_runner.call_args[0][0]
        assert "Get-AppxPackage -AllUsers -PackageFullName 'Microsoft.YourPhone_1.2.3.4_x64__8wekyb3d8bbwe'" in script_arg
        assert "Remove-AppxPackage -AllUsers" in script_arg

    async def test_removes_uwp_app_using_name_as_fallback(self, optimizer: WindowsOptimizer, mock_powershell_runner: MagicMock):
        """Проверяет, что если PackageFullName отсутствует, используется поиск по имени."""
        # GIVEN
        action_plan = [{
            "type": "uwp_app",
            "id": "Microsoft.YourPhone",
            # package_full_name отсутствует
            "action": "remove",
            "reason": "Bloatware"
        }]

        # WHEN
        await optimizer.execute_action_plan(action_plan, lambda p, t: None)

        # THEN
        mock_powershell_runner.assert_called_once()
        script_arg = mock_powershell_runner.call_args[0][0]
        assert "Get-AppxPackage -AllUsers -Name '*Microsoft.YourPhone*'" in script_arg

    async def test_executes_multiple_actions_in_one_script(self, optimizer: WindowsOptimizer, mock_powershell_runner: MagicMock):
        """Проверяет, что несколько действий объединяются в один PowerShell-скрипт."""
        # GIVEN
        action_plan = [
            {"type": "service", "id": "Service1", "action": "disable"},
            {"type": "uwp_app", "id": "App1", "action": "remove"},
        ]
        
        # WHEN
        await optimizer.execute_action_plan(action_plan, lambda p, t: None)
        
        # THEN
        mock_powershell_runner.assert_called_once()
        script_arg = mock_powershell_runner.call_args[0][0]
        assert "Set-Service -Name 'Service1' -StartupType Disabled" in script_arg
        assert "Get-AppxPackage -AllUsers -Name '*App1*'" in script_arg

    async def test_does_not_run_script_for_empty_plan(self, optimizer: WindowsOptimizer, mock_powershell_runner: MagicMock):
        """Проверяет, что для пустого плана не происходит вызовов PowerShell."""
        # GIVEN
        empty_plan = []
        
        # WHEN
        summary = await optimizer.execute_action_plan(empty_plan, lambda p, t: None)
        
        # THEN
        # PowerShell не вызывался
        mock_powershell_runner.assert_not_called()
        # Отчет пустой
        assert not summary.get("disabled_services")
        assert not summary.get("removed_apps")

    # Тест на обработку ошибок PowerShell можно убрать, так как мы мокаем
    # сам метод _run_powershell_script. Его внутреннюю логику нужно
    # тестировать отдельно, если бы она была сложной.