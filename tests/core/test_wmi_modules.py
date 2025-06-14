# tests/core/test_wmi_modules.py
"""
Тесты для модулей, взаимодействующих с WMI.

Эти тесты проверяют, что:
- Данные из WMI корректно собираются и парсятся.
- Ошибки при взаимодействии с WMI правильно обрабатываются и логируются.
"""

import pytest
import wmi
from unittest.mock import MagicMock, AsyncMock

# Импортируем тестируемые классы
from src.winspector.core.modules.user_profiler import UserProfiler
from src.winspector.core.modules.windows_optimizer import WindowsOptimizer
from src.winspector.core.modules.wmi_base import WMIBase

# Помечаем все тесты в этом файле как асинхронные
pytestmark = pytest.mark.asyncio


@pytest.fixture
def mock_wmi_instance(mocker) -> MagicMock:
    """
    Общая фикстура для мокинга wmi.WMI().
    Она заменяет свойство wmi_instance в базовом классе WMIBase,
    чтобы все дочерние классы использовали наш мок.
    """
    mock_instance = MagicMock()
    # Используем mocker.patch.object для замены property в базовом классе
    mocker.patch.object(
        WMIBase, 'wmi_instance', new_callable=mocker.PropertyMock, return_value=mock_instance
    )
    return mock_instance


# --- Тесты для UserProfiler ---

class TestUserProfilerWMI:

    @pytest.fixture
    def profiler(self, mocker) -> UserProfiler:
        """Фикстура, создающая UserProfiler и мокающая его не-WMI зависимости."""
        # Мокаем другие методы, чтобы изолировать тест на WMI
        mocker.patch('src.winspector.core.modules.user_profiler.UserProfiler._get_installed_software_from_registry', return_value=["Steam", "VS Code"])
        mocker.patch('src.winspector.core.modules.user_profiler.UserProfiler._scan_for_profile_markers', return_value={"Gamer": ["steam_library"]})
        return UserProfiler(profiler_config={})

    async def test_get_system_profile_success(self, profiler: UserProfiler, mock_wmi_instance: MagicMock):
        """Тест успешного сбора полного профиля системы, включая данные WMI."""
        # GIVEN: Настраиваем, что вернет наш мок WMI
        mock_processor = MagicMock(Name="Intel Core i9-9900K")
        mock_wmi_instance.Win32_Processor.return_value = [mock_processor]
        
        mock_gpu = MagicMock(Name="NVIDIA GeForce RTX 3080", AdapterCompatibility="NVIDIA")
        mock_wmi_instance.Win32_VideoController.return_value = [mock_gpu]

        mock_memory = MagicMock(TotalPhysicalMemory=str(32 * (1024**3))) # WMI возвращает строки для чисел
        mock_wmi_instance.Win32_ComputerSystem.return_value = [mock_memory]

        # WHEN
        profile = await profiler.get_system_profile()

        # THEN
        assert profile['hardware']['cpu'] == "Intel Core i9-9900K"
        assert "NVIDIA GeForce RTX 3080" in profile['hardware']['gpu']
        assert profile['hardware']['ram_gb'] == 32

    async def test_wmi_error_handling(self, profiler: UserProfiler, mock_wmi_instance: MagicMock, mocker):
        """Проверяет, что при ошибке WMI сбор профиля продолжается, а ошибка логируется."""
        # GIVEN: Настраиваем мок WMI на вызов ошибки
        mock_wmi_instance.Win32_Processor.side_effect = wmi.x_wmi("Access Denied")
        mock_log_error = mocker.patch("src.winspector.core.modules.user_profiler.logger.error")

        # WHEN
        profile = await profiler.get_system_profile()

        # THEN
        assert profile["hardware"] == {} # Секция hardware пуста из-за ошибки
        assert profile["installed_software"] == ["Steam", "VS Code"] # Остальные данные на месте
        mock_log_error.assert_called_once()
        assert "Ошибка при сборе данных об оборудовании" in mock_log_error.call_args[0][0]


# --- Тесты для WindowsOptimizer ---

class TestWindowsOptimizerWMI:

    @pytest.fixture
    def optimizer(self, mocker) -> WindowsOptimizer:
        """Фикстура, создающая WindowsOptimizer и мокающая его не-WMI зависимости."""
        # Мокаем PowerShell вызов для сбора UWP-приложений
        mocker.patch('src.winspector.core.modules.windows_optimizer.WindowsOptimizer._collect_uwp_apps', return_value=[{"id": "XboxApp"}])
        return WindowsOptimizer()

    async def test_get_system_components_success(self, optimizer: WindowsOptimizer, mock_wmi_instance: MagicMock):
        """Тест успешного сбора компонентов системы, включая службы из WMI."""
        # GIVEN
        mock_service1 = MagicMock(Name="UselessSvc", PathName="c:\\path\\to\\bloatware.exe", State="Running")
        mock_service2 = MagicMock(Name="CriticalSvc", PathName="c:\\windows\\system32\\svchost.exe", State="Running")
        mock_wmi_instance.Win32_Service.return_value = [mock_service1, mock_service2]
        
        # WHEN
        components = await optimizer.get_system_components()

        # THEN
        # Проверяем, что служба Microsoft отфильтрована, а другая - нет
        assert len(components["services"]) == 1
        assert components["services"][0]["name"] == "UselessSvc"
        # Проверяем, что UWP приложения на месте
        assert len(components["uwp_apps"]) == 1
        assert components["uwp_apps"][0]["id"] == "XboxApp"

    async def test_wmi_error_handling(self, optimizer: WindowsOptimizer, mock_wmi_instance: MagicMock, mocker):
        """Проверяет, что при ошибке WMI сбор компонентов продолжается."""
        # GIVEN
        mock_wmi_instance.Win32_Service.side_effect = AttributeError("WMI Service Failed")
        mock_log_error = mocker.patch("src.winspector.core.modules.windows_optimizer.logger.error")

        # WHEN
        components = await optimizer.get_system_components()

        # THEN
        assert components["services"] == [] # Список служб пуст
        assert len(components["uwp_apps"]) == 1 # UWP приложения на месте
        mock_log_error.assert_called_once()
        assert "Не удалось получить список служб через WMI" in mock_log_error.call_args[0][0]