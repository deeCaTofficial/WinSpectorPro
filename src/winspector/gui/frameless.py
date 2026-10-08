"""
Окно со своим заголовком, которое ведёт себя как обычное окно Windows.

Флаг Qt `FramelessWindowHint` убирает системный заголовок, но вместе с ним
пропадает всё, чего ждут от окна: тень, привязка к краям экрана (Aero Snap),
анимация сворачивания, изменение размера за края. Поэтому окну возвращаются
системные стили рамки, а на WM_NCCALCSIZE вся площадь окна отдаётся
содержимому: рамки не видно, но для Windows это по-прежнему окно с
заголовком.

Где у окна заголовок, края и кнопка «Развернуть», Windows узнаёт из
WM_NCHITTEST. Так перетаскивание, двойной щелчок, меню окна по правой кнопке
и привязка работают средствами системы, а при наведении на «Развернуть»
Windows 11 показывает макеты привязки.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QWidget

from .widgets.title_bar import TitleBar

logger = logging.getLogger(__name__)

# Ответы на WM_NCHITTEST.
HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT, HTRIGHT, HTTOP = 10, 11, 12
HTTOPLEFT, HTTOPRIGHT = 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17

# Полоса у края окна, за которую меняется размер, и длина угла, за который
# размер меняется сразу по двум сторонам, px.
RESIZE_BORDER = 6
RESIZE_CORNER = 16

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCLBUTTONUP = 0x00A2
_WM_NCLBUTTONDBLCLK = 0x00A3
_WM_NCMOUSELEAVE = 0x02A2

_GWL_STYLE = -16
_WS_CAPTION = 0x00C00000
_WS_SYSMENU = 0x00080000
_WS_THICKFRAME = 0x00040000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_FRAME_ONLY = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # без сдвига, только рамка

_SM_CXSIZEFRAME, _SM_CYSIZEFRAME, _SM_CXPADDEDBORDER = 32, 33, 92
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_ROUND = 2

_ABM_GETSTATE, _ABM_GETAUTOHIDEBAREX = 0x04, 0x0B
_ABS_AUTOHIDE = 0x01
_ABE_LEFT, _ABE_TOP, _ABE_RIGHT, _ABE_BOTTOM = range(4)
# Сколько оставить у края с автоскрываемой панелью задач, чтобы она
# выезжала под развёрнутым окном.
_AUTOHIDE_GAP = 2


def hit_test(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    maximized: bool,
    caption_height: int,
    on_control: bool,
    on_maximize: bool,
) -> int:
    """Чем для Windows является точка окна: краем, заголовком или содержимым."""
    if not maximized:
        left, right = x < RESIZE_BORDER, x >= width - RESIZE_BORDER
        top, bottom = y < RESIZE_BORDER, y >= height - RESIZE_BORDER
        # Угол ловится на длине RESIZE_CORNER: попасть в квадрат 6×6 трудно.
        near_left, near_right = x < RESIZE_CORNER, x >= width - RESIZE_CORNER
        near_top, near_bottom = y < RESIZE_CORNER, y >= height - RESIZE_CORNER
        if (top and near_left) or (left and near_top):
            return HTTOPLEFT
        if (top and near_right) or (right and near_top):
            return HTTOPRIGHT
        if (bottom and near_left) or (left and near_bottom):
            return HTBOTTOMLEFT
        if (bottom and near_right) or (right and near_bottom):
            return HTBOTTOMRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
    if y < caption_height:
        if on_maximize:
            return HTMAXBUTTON
        if on_control:
            return HTCLIENT
        return HTCAPTION
    return HTCLIENT


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


def _signed_word(value: int) -> int:
    return ctypes.c_short(value & 0xFFFF).value


class NativeFrame:
    """Системное поведение окна без системного заголовка."""

    def __init__(self, window: QWidget, title_bar: TitleBar) -> None:
        self._window = window
        self._title_bar = title_bar
        # Свои копии библиотек: настройка типов не задевает остальной код.
        self._user32 = ctypes.WinDLL("user32")
        self._user32.MonitorFromWindow.restype = wintypes.HMONITOR
        self._user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
        self._hwnd = int(window.winId())
        self._restore_frame_styles()

    def _restore_frame_styles(self) -> None:
        user32 = self._user32
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        style = user32.GetWindowLongW(wintypes.HWND(self._hwnd), _GWL_STYLE)
        style |= _WS_CAPTION | _WS_SYSMENU | _WS_THICKFRAME | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX
        user32.SetWindowLongW(wintypes.HWND(self._hwnd), _GWL_STYLE, ctypes.c_long(style).value)
        try:
            dwm = ctypes.WinDLL("dwmapi")
            # Хотя бы пиксель системной рамки в окне возвращает ему тень (его
            # закрывает содержимое); Windows 11 вдобавок скругляет углы.
            margins = _MARGINS(0, 0, 1, 0)
            dwm.DwmExtendFrameIntoClientArea(wintypes.HWND(self._hwnd), ctypes.byref(margins))
            corner = ctypes.c_int(_DWMWCP_ROUND)
            dwm.DwmSetWindowAttribute(
                wintypes.HWND(self._hwnd),
                _DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner),
                ctypes.sizeof(corner),
            )
        except (AttributeError, OSError):
            logger.debug("DWM недоступен: окно без тени.", exc_info=True)
        # Пересчёт рамки: с этого момента WM_NCCALCSIZE отдаёт окно целиком.
        user32.SetWindowPos(wintypes.HWND(self._hwnd), None, 0, 0, 0, 0, _SWP_FRAME_ONLY)

    def handle(self, message) -> tuple[bool, int] | None:
        """Ответ на сообщение Windows или None, если его обработает Qt."""
        msg = wintypes.MSG.from_address(int(message))
        kind = msg.message
        if kind == _WM_NCCALCSIZE:
            self._fit_client_area(msg)
            return True, 0
        if kind == _WM_NCHITTEST:
            result = self._hit_test(msg.lParam)
            self._title_bar.set_maximize_native_state(
                hover=result == HTMAXBUTTON, pressed=self._title_bar.maximize_button.native_pressed
            )
            return True, result
        if msg.wParam == HTMAXBUTTON and kind in (_WM_NCLBUTTONDOWN, _WM_NCLBUTTONDBLCLK):
            # Нажатие «Развернуть» обрабатываем сами: иначе Windows рисует
            # поверх свою кнопку старого вида.
            self._title_bar.set_maximize_native_state(hover=True, pressed=True)
            return True, 0
        if msg.wParam == HTMAXBUTTON and kind == _WM_NCLBUTTONUP:
            if self._title_bar.maximize_button.native_pressed:
                self._title_bar.set_maximize_native_state(hover=True)
                self._title_bar.toggle_maximized()
            return True, 0
        if kind == _WM_NCMOUSELEAVE:
            self._title_bar.set_maximize_native_state(hover=False)
        return None

    def _hit_test(self, lparam: int) -> int:
        rect = wintypes.RECT()
        self._user32.GetWindowRect(wintypes.HWND(self._hwnd), ctypes.byref(rect))
        # Координаты от Windows — в пикселях экрана, у Qt — с учётом масштаба.
        ratio = self._window.devicePixelRatioF() or 1.0
        x = (_signed_word(lparam) - rect.left) / ratio
        y = (_signed_word(lparam >> 16) - rect.top) / ratio
        point = QPoint(int(x), int(y))
        bar = self._title_bar
        in_bar = bar.mapFrom(self._window, point)
        return hit_test(
            point.x(),
            point.y(),
            self._window.width(),
            self._window.height(),
            maximized=self._window.isMaximized(),
            caption_height=bar.mapTo(self._window, QPoint(0, bar.height())).y(),
            on_control=bar.is_control_at(in_bar),
            on_maximize=bar.is_maximize_at(in_bar),
        )

    def _fit_client_area(self, msg: wintypes.MSG) -> None:
        """Всё окно — содержимое; развёрнутое окно не заходит за край экрана."""
        if not msg.wParam:
            return
        params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
        rect = params.rgrc[0]
        if not self._user32.IsZoomed(wintypes.HWND(self._hwnd)):
            return
        # Развёрнутое окно Windows сдвигает за край экрана на толщину рамки.
        dpi = self._user32.GetDpiForWindow(wintypes.HWND(self._hwnd))
        padded = self._user32.GetSystemMetricsForDpi(_SM_CXPADDEDBORDER, dpi)
        frame_x = self._user32.GetSystemMetricsForDpi(_SM_CXSIZEFRAME, dpi) + padded
        frame_y = self._user32.GetSystemMetricsForDpi(_SM_CYSIZEFRAME, dpi) + padded
        rect.left += frame_x
        rect.right -= frame_x
        rect.top += frame_y
        rect.bottom -= frame_y
        self._leave_room_for_autohide_taskbar(rect)

    def _leave_room_for_autohide_taskbar(self, rect: wintypes.RECT) -> None:
        shell32 = ctypes.WinDLL("shell32")
        data = _APPBARDATA(cbSize=ctypes.sizeof(_APPBARDATA))
        if not shell32.SHAppBarMessage(_ABM_GETSTATE, ctypes.byref(data)) & _ABS_AUTOHIDE:
            return
        monitor = self._user32.MonitorFromWindow(wintypes.HWND(self._hwnd), 2)
        info = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
        if not self._user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return
        for edge in (_ABE_LEFT, _ABE_TOP, _ABE_RIGHT, _ABE_BOTTOM):
            data = _APPBARDATA(cbSize=ctypes.sizeof(_APPBARDATA), uEdge=edge, rc=info.rcMonitor)
            if not shell32.SHAppBarMessage(_ABM_GETAUTOHIDEBAREX, ctypes.byref(data)):
                continue
            if edge == _ABE_LEFT:
                rect.left += _AUTOHIDE_GAP
            elif edge == _ABE_TOP:
                rect.top += _AUTOHIDE_GAP
            elif edge == _ABE_RIGHT:
                rect.right -= _AUTOHIDE_GAP
            else:
                rect.bottom -= _AUTOHIDE_GAP


def attach(window: QWidget, title_bar: TitleBar) -> NativeFrame | None:
    """Включает системное поведение окна, если приложение работает в Windows."""
    from PyQt6.QtGui import QGuiApplication

    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return None
    try:
        return NativeFrame(window, title_bar)
    except (AttributeError, OSError):
        logger.warning("Не удалось настроить рамку окна.", exc_info=True)
        return None
