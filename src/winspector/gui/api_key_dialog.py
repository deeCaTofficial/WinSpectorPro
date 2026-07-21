# src/winspector/gui/api_key_dialog.py
"""
Окно настройки доступа к ИИ.

Показывается при первом запуске, если ключ не найден, и доступно позже из
главного окна. Без него скачавший программу пользователь не имел никакой
возможности задать ключ — предполагалось, что он вручную создаст файл `.env`
рядом с исполняемым файлом.

Ключ вводится в поле с маскировкой и уходит в защищённое хранилище
(`core.credentials`). В журнал он не попадает.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PyQt6.QtGui import QColor, QDesktopServices, QMouseEvent, QShowEvent
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import credentials

logger = logging.getLogger(__name__)

AI_STUDIO_URL = "https://aistudio.google.com/app/apikey"

_DIALOG_STYLE = """
#ApiKeyDialog {
    background: transparent;
    color: #f4f8ff;
    font-family: "Segoe UI";
}

#DialogSurface {
    background-color: rgba(18, 25, 40, 252);
    border: 1px solid rgba(66, 145, 255, 150);
    border-radius: 20px;
}

#AccentLine {
    background-color: qlineargradient(
        x1:0, y1:0.5, x2:1, y2:0.5,
        stop:0 rgba(30, 144, 255, 0),
        stop:0.18 #1e90ff,
        stop:0.72 #00bfff,
        stop:1 rgba(0, 191, 255, 0)
    );
    border: none;
    border-radius: 2px;
}

#WindowMark {
    color: #4bbcff;
    font-size: 13px;
    font-weight: 700;
}

#WindowCaption {
    color: #aebbd0;
    font-size: 12px;
    font-weight: 600;
}

#CloseButton {
    min-width: 30px;
    max-width: 30px;
    min-height: 30px;
    max-height: 30px;
    padding: 0;
    color: #9caac0;
    background: rgba(255, 255, 255, 8);
    border: 1px solid rgba(116, 151, 197, 45);
    border-radius: 15px;
    font-size: 16px;
    font-weight: 400;
}

#CloseButton:hover {
    color: white;
    background: rgba(231, 76, 95, 170);
    border-color: rgba(255, 116, 132, 210);
}

#AiBadge {
    min-width: 44px;
    max-width: 44px;
    min-height: 44px;
    max-height: 44px;
    color: white;
    background-color: qradialgradient(
        cx:0.45, cy:0.38, radius:0.8,
        stop:0 #31c8ff,
        stop:0.48 #147ee7,
        stop:1 #0c3f8e
    );
    border: 1px solid rgba(126, 211, 255, 190);
    border-radius: 22px;
    font-size: 20px;
    font-weight: 700;
}

#DialogTitle {
    color: #ffffff;
    font-size: 23px;
    font-weight: 650;
}

#KeyCard {
    background: rgba(5, 12, 25, 145);
    border: 1px solid rgba(74, 116, 168, 100);
    border-radius: 14px;
}

#SectionTitle {
    color: #eef6ff;
    font-size: 13px;
    font-weight: 650;
}

QLineEdit#ApiKeyInput {
    min-height: 44px;
    padding: 0 14px;
    color: #f3f8ff;
    selection-color: white;
    selection-background-color: #167dcc;
    background: rgba(3, 8, 17, 190);
    border: 1px solid rgba(94, 124, 165, 115);
    border-radius: 10px;
    font-size: 13px;
}

QLineEdit#ApiKeyInput:hover {
    border-color: rgba(71, 155, 241, 150);
}

QLineEdit#ApiKeyInput:focus {
    border: 1px solid #2fa8ff;
    background: rgba(4, 12, 25, 220);
}

#RevealButton {
    min-width: 92px;
    max-width: 92px;
    min-height: 44px;
    padding: 0 12px;
    color: #bcd5ef;
    background: rgba(29, 67, 108, 105);
    border: 1px solid rgba(65, 137, 210, 115);
    border-radius: 10px;
    font-size: 12px;
    font-weight: 600;
}

#RevealButton:hover, #RevealButton:checked {
    color: white;
    background: rgba(28, 121, 205, 165);
    border-color: #35a9ff;
}

#StudioButton {
    min-height: 38px;
    padding: 0 16px;
    color: #65c7ff;
    background: transparent;
    border: 1px solid rgba(64, 164, 235, 105);
    border-radius: 9px;
    font-size: 12px;
    font-weight: 650;
}

#StudioButton:hover {
    color: white;
    background: rgba(26, 111, 181, 90);
    border-color: #45b8ff;
}

#StatusLabel {
    color: #ffb5bd;
    background: rgba(174, 48, 65, 60);
    border: 1px solid rgba(238, 83, 103, 100);
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 11px;
}

#SecurityNote {
    color: #83d7bb;
    font-size: 11px;
    font-weight: 550;
}

#PrimaryButton {
    min-height: 42px;
    padding: 0 22px;
    color: white;
    background-color: qlineargradient(
        x1:0, y1:0.5, x2:1, y2:0.5,
        stop:0 #086ac5,
        stop:1 #00a8e8
    );
    border: 1px solid #32baff;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 700;
}

#PrimaryButton:hover {
    background-color: qlineargradient(
        x1:0, y1:0.5, x2:1, y2:0.5,
        stop:0 #0c7bdd,
        stop:1 #13b9f2
    );
    border-color: #78d7ff;
}

#PrimaryButton:pressed {
    background: #075da9;
}

#SecondaryButton, #DangerButton {
    min-height: 42px;
    padding: 0 18px;
    color: #b5c3d7;
    background: rgba(35, 47, 67, 125);
    border: 1px solid rgba(98, 119, 151, 95);
    border-radius: 10px;
    font-size: 12px;
    font-weight: 650;
}

#SecondaryButton:hover {
    color: white;
    background: rgba(51, 79, 112, 150);
    border-color: rgba(87, 157, 225, 170);
}

#DangerButton {
    color: #e5a8af;
}

#DangerButton:hover {
    color: white;
    background: rgba(161, 48, 63, 120);
    border-color: rgba(231, 91, 108, 170);
}
"""


class ApiKeyDialog(QDialog):
    """Диалог ввода ключа Gemini с возможностью продолжить без ИИ."""

    def __init__(self, parent: QWidget | None = None, *, first_run: bool = True) -> None:
        super().__init__(parent)
        self._offline_chosen = False
        self._drag_offset: QPoint | None = None
        self._fade_animation: QPropertyAnimation | None = None

        self.setWindowTitle("Настройка ИИ — WinSpector Pro")
        self.setObjectName("ApiKeyDialog")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(590, 410)
        self.resize(610, 430)
        self.setModal(True)
        self.setStyleSheet(_DIALOG_STYLE)

        self._build_ui(first_run)

    # --- Интерфейс --------------------------------------------------------

    def _build_ui(self, first_run: bool) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(18, 18, 18, 18)

        surface = QFrame()
        surface.setObjectName("DialogSurface")
        shadow = QGraphicsDropShadowEffect(surface)
        shadow.setBlurRadius(38)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 185))
        surface.setGraphicsEffect(shadow)
        outer_layout.addWidget(surface)

        layout = QVBoxLayout(surface)
        layout.setContentsMargins(26, 16, 26, 22)
        layout.setSpacing(13)

        header = QHBoxLayout()
        header.setSpacing(8)
        mark = QLabel("◆")
        mark.setObjectName("WindowMark")
        caption = QLabel("WINSPECTOR PRO")
        caption.setObjectName("WindowCaption")
        close_button = QPushButton("×")
        close_button.setObjectName("CloseButton")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.setToolTip("Закрыть")
        close_button.clicked.connect(self._continue_offline)
        header.addWidget(mark)
        header.addWidget(caption)
        header.addStretch()
        header.addWidget(close_button)
        layout.addLayout(header)

        accent = QFrame()
        accent.setObjectName("AccentLine")
        accent.setFixedHeight(2)
        layout.addWidget(accent)

        hero = QHBoxLayout()
        hero.setSpacing(13)
        badge = QLabel("✦")
        badge.setObjectName("AiBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(badge)

        title = QLabel("Подключение ИИ" if first_run else "Настройка Gemini")
        title.setObjectName("DialogTitle")
        hero.addWidget(title)
        hero.addStretch()
        layout.addLayout(hero)

        key_card = QFrame()
        key_card.setObjectName("KeyCard")
        key_layout = QVBoxLayout(key_card)
        key_layout.setContentsMargins(16, 14, 16, 16)
        key_layout.setSpacing(10)

        section_title = QLabel("API-ключ Gemini")
        section_title.setObjectName("SectionTitle")
        key_layout.addWidget(section_title)

        self.key_input = QLineEdit()
        self.key_input.setObjectName("ApiKeyInput")
        self.key_input.setPlaceholderText("Вставьте API-ключ Gemini")
        # Ключ — секрет, поэтому по умолчанию скрыт; кнопка справа
        # позволяет проверить, что вставилось целиком.
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.returnPressed.connect(self._save)

        show_button = QPushButton("Показать")
        show_button.setObjectName("RevealButton")
        show_button.setCheckable(True)
        show_button.toggled.connect(self._toggle_echo)
        self.show_button = show_button

        input_row = QHBoxLayout()
        input_row.setSpacing(9)
        input_row.addWidget(self.key_input, 1)
        input_row.addWidget(show_button)
        key_layout.addLayout(input_row)

        get_key_button = QPushButton("Получить бесплатный ключ  ↗")
        get_key_button.setObjectName("StudioButton")
        get_key_button.setCursor(Qt.CursorShape.PointingHandCursor)
        get_key_button.clicked.connect(self._open_ai_studio)
        key_layout.addWidget(get_key_button)

        self.status_label = QLabel()
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        key_layout.addWidget(self.status_label)

        security_note = QLabel("✓  Зашифрованное хранение на этом компьютере")
        security_note.setObjectName("SecurityNote")
        key_layout.addWidget(security_note)
        layout.addWidget(key_card)

        layout.addStretch(1)
        layout.addLayout(self._build_buttons(first_run))

        if not credentials.is_supported():
            self._show_error(
                "Защищённое хранилище недоступно (не установлен pywin32). "
                "Ключ можно задать переменной окружения GEMINI_API_KEY."
            )

    def _build_buttons(self, first_run: bool) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        if not first_run and credentials.load_stored_key():
            forget_button = QPushButton("Удалить ключ")
            forget_button.setObjectName("DangerButton")
            forget_button.clicked.connect(self._forget)
            row.addWidget(forget_button)

        row.addStretch()

        offline_button = QPushButton("Без ИИ" if first_run else "Закрыть")
        offline_button.setObjectName("SecondaryButton")
        offline_button.clicked.connect(self._continue_offline)
        row.addWidget(offline_button)

        save_button = QPushButton("Подключить ИИ")
        save_button.setObjectName("PrimaryButton")
        save_button.setDefault(True)
        save_button.clicked.connect(self._save)
        row.addWidget(save_button)

        return row

    # --- Действия ---------------------------------------------------------

    def _toggle_echo(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.key_input.setEchoMode(mode)
        self.show_button.setText("Скрыть" if visible else "Показать")

    def _open_ai_studio(self) -> None:
        from PyQt6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl(AI_STUDIO_URL))

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.show()
        # Скрытая метка не входит в исходный sizeHint. После её появления
        # диалог должен вырасти, иначе Qt сжимает сообщение до тонкой полосы.
        QTimer.singleShot(0, self._fit_status_message)

    def _fit_status_message(self) -> None:
        """Даёт сообщению об ошибке необходимую высоту без растягивания ширины."""
        self.ensurePolished()
        if layout := self.layout():
            layout.activate()
        required_height = max(self.minimumHeight(), self.sizeHint().height())
        if required_height > self.height():
            self.resize(self.width(), required_height)

    def _save(self) -> None:
        key = self.key_input.text().strip()
        if not key:
            self._show_error("Введите ключ или выберите «Без ИИ».")
            return

        try:
            credentials.save_api_key(key)
        except (ValueError, RuntimeError) as exc:
            # Текст ключа в сообщение не попадает.
            self._show_error(str(exc))
            return

        # Клиент мог быть создан со старым ключом.
        from ..core.modules.ai_base import AIBase

        AIBase.reset_client()

        self._offline_chosen = False
        self.accept()

    def _forget(self) -> None:
        confirmation = QMessageBox.question(
            self,
            "Удалить ключ",
            "Ключ будет удалён с этого компьютера. Приложение продолжит "
            "работать без персонализации. Продолжить?",
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        credentials.delete_api_key()

        from ..core.modules.ai_base import AIBase

        AIBase.reset_client()

        self._offline_chosen = True
        self.accept()

    def _continue_offline(self) -> None:
        self._offline_chosen = True
        self.accept()

    # --- Поведение безрамочного окна -------------------------------------

    def showEvent(self, event: QShowEvent) -> None:
        """Мягко проявляет диалог поверх основного окна."""
        super().showEvent(event)
        self.setWindowOpacity(0.0)
        animation = QPropertyAnimation(self, b"windowOpacity", self)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_animation = animation
        animation.start()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # --- Результат --------------------------------------------------------

    @property
    def offline_chosen(self) -> bool:
        """Выбрал ли пользователь работу без ИИ."""
        return self._offline_chosen


def ensure_api_key_configured(parent: QWidget | None = None) -> bool:
    """
    Показывает диалог, если ключ ещё не задан.

    Returns:
        True, если ИИ настроен и доступен; False — работать без него.
    """
    if credentials.has_api_key():
        return True

    dialog = ApiKeyDialog(parent, first_run=True)
    dialog.exec()

    configured = credentials.has_api_key()
    logger.info("Настройка ИИ завершена: %s.", "ключ задан" if configured else "офлайн-режим")
    return configured
