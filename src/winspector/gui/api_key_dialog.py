# src/winspector/gui/api_key_dialog.py
"""
Окно настройки доступа к ИИ.

Открывается из раздела Gemini в настройках приложения.

Ключ вводится в поле с маскировкой и уходит в защищённое хранилище
(`core.credentials`). В журнал он не попадает.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import TYPE_CHECKING

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import credentials
from ..core.modules.ai_base import configured_model
from . import message_dialog
from .language import get_language, localize
from .theme import MUTED, TEXT, glyph_icon
from .widgets.setting_row import ICON_SIZE, set_row_icon, setting_row

if TYPE_CHECKING:
    from ..core.modules.ai_base import KeyCheckResult

logger = logging.getLogger(__name__)

AI_STUDIO_URL = "https://aistudio.google.com/apikey"

# Значки из системного шрифта значков Windows.
_GLYPH_GEMINI = "\ue99a"
_GLYPH_KEY = "\ue192"
_GLYPH_SHOW = "\ue890"
_GLYPH_HIDE = "\ued1a"
_GLYPH_OPEN = "\ue8a7"

# Как часто окно смотрит, закончилась ли проверка ключа в фоновом потоке.
_CHECK_POLL_MS = 100


def _default_key_checker(api_key: str, *, language: str) -> KeyCheckResult:
    # SDK Gemini тяжёлый, поэтому модуль импортируется только при проверке.
    from ..core.modules.ai_base import check_api_key

    return check_api_key(api_key, language=language)


class ApiKeyDialog(QDialog):
    """Диалог ввода ключа Gemini с возможностью продолжить без ИИ."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        first_run: bool = True,
        key_checker: Callable[[str], KeyCheckResult] | None = None,
        language: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.language = language or get_language()
        self._offline_chosen = False

        # Ключ проверяется запросом к Gemini в фоновом потоке, чтобы окно не
        # замирало; таймер забирает результат уже в потоке интерфейса.
        self._key_checker = key_checker or functools.partial(
            _default_key_checker, language=self.language
        )
        self._pending_check: tuple[str, Future[KeyCheckResult]] | None = None
        self._check_timer = QTimer(self)
        self._check_timer.setInterval(_CHECK_POLL_MS)
        self._check_timer.timeout.connect(self._poll_check)

        self.setWindowTitle(self._t("Gemini — WinSpector Pro", "Gemini — WinSpector Pro"))
        self.setObjectName("ApiKeyDialog")
        self.setMinimumWidth(520)
        self.setModal(True)

        self._build_ui(first_run)
        # Высота — по содержимому; сообщение о проверке добавит её само.
        self.resize(560, self._content_height(560))

    def _t(self, russian: str, english: str) -> str:
        return localize(self.language, russian, english)

    # --- Интерфейс --------------------------------------------------------

    def _build_ui(self, first_run: bool) -> None:
        # Заголовок уже в рамке окна; содержимое — те же строки-карточки,
        # что в настройках.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(6)

        get_key_button = QPushButton(self._t("Получить ключ", "Get a key"))
        get_key_button.setIcon(glyph_icon(_GLYPH_OPEN, 14, MUTED, TEXT, gap=6))
        get_key_button.setIconSize(QSize(14 + 6, 14))
        get_key_button.setToolTip(AI_STUDIO_URL)
        get_key_button.clicked.connect(self._open_ai_studio)
        row, title, subtitle = setting_row(_GLYPH_GEMINI, get_key_button)
        title.setText(self._t("API-ключ Gemini", "Gemini API key"))
        subtitle.setText(
            self._t(
                f"Google AI Studio, доступ к {configured_model()}",
                f"Google AI Studio, access to {configured_model()}",
            )
        )
        layout.addWidget(row)

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText(
            self._t("Вставьте API-ключ Gemini", "Paste your Gemini API key")
        )
        self.key_input.setAccessibleName(self._t("API-ключ Gemini", "Gemini API key"))
        # Ключ — секрет, поэтому по умолчанию скрыт; глаз справа позволяет
        # проверить, что вставилось целиком.
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.returnPressed.connect(self._save)

        # Глаз внутри поля, как в поле пароля Windows.
        self.show_button = QAction(self)
        self.show_button.setCheckable(True)
        self.show_button.toggled.connect(self._toggle_echo)
        self.key_input.addAction(self.show_button, QLineEdit.ActionPosition.TrailingPosition)
        self._toggle_echo(False)

        key_card = QFrame()
        key_card.setObjectName("SettingRow")
        card = QHBoxLayout(key_card)
        card.setContentsMargins(16, 12, 14, 12)
        card.setSpacing(14)
        key_icon = QLabel()
        key_icon.setObjectName("RowIcon")
        # Высота как у поля: значок встаёт по центру строки ввода.
        key_icon.setFixedSize(ICON_SIZE, 36)
        set_row_icon(key_icon, _GLYPH_KEY)
        field = QVBoxLayout()
        field.setSpacing(6)
        field.addWidget(self.key_input)
        security_note = QLabel(
            self._t(
                "Хранится зашифрованным только на этом компьютере",
                "Stored encrypted, only on this computer",
            )
        )
        security_note.setObjectName("RowSubtitle")
        security_note.setWordWrap(True)
        field.addWidget(security_note)
        # Значок выровнен по полю, а не по центру карточки.
        card.addWidget(key_icon, 0, Qt.AlignmentFlag.AlignTop)
        card.addLayout(field, 1)
        layout.addWidget(key_card)

        layout.addSpacing(4)
        self.status_label = QLabel()
        self.status_label.setObjectName("KeyStatus")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        layout.addStretch(1)
        layout.addSpacing(14)
        layout.addLayout(self._build_buttons(first_run))

        if not credentials.is_supported():
            self._show_error(
                "Защищённое хранилище недоступно (не установлен pywin32). "
                "Ключ можно задать переменной окружения GEMINI_API_KEY."
                if self.language == "ru"
                else "Secure storage is unavailable (pywin32 is missing). "
                "You can set the GEMINI_API_KEY environment variable."
            )

    def _build_buttons(self, first_run: bool) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        if not first_run and credentials.load_stored_key():
            forget_button = QPushButton(self._t("Удалить ключ", "Remove key"))
            forget_button.setObjectName("DangerButton")
            forget_button.clicked.connect(self._forget)
            row.addWidget(forget_button)

        row.addStretch()

        offline_button = QPushButton(
            self._t("Без ИИ", "Without AI") if first_run else self._t("Закрыть", "Close")
        )
        offline_button.setObjectName("SecondaryButton")
        offline_button.setMinimumWidth(110)
        offline_button.clicked.connect(self._continue_offline)
        row.addWidget(offline_button)

        save_button = QPushButton(self._t("Подключить", "Connect"))
        save_button.setObjectName("PrimaryButton")
        save_button.setMinimumWidth(140)
        save_button.setDefault(True)
        save_button.clicked.connect(self._save)
        row.addWidget(save_button)
        self.save_button = save_button

        return row

    # --- Действия ---------------------------------------------------------

    def _toggle_echo(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.key_input.setEchoMode(mode)
        self.show_button.setIcon(glyph_icon(_GLYPH_HIDE if visible else _GLYPH_SHOW, 16))
        caption = (
            self._t("Скрыть ключ", "Hide key") if visible else self._t("Показать ключ", "Show key")
        )
        self.show_button.setToolTip(caption)
        self.show_button.setText(caption)

    def _open_ai_studio(self) -> None:
        from PyQt6.QtCore import QUrl

        QDesktopServices.openUrl(QUrl(AI_STUDIO_URL))

    def _show_error(self, message: str) -> None:
        self._show_status(message, error=True)

    def _show_status(self, message: str, *, error: bool) -> None:
        self.status_label.setProperty("state", "error" if error else "info")
        # Стиль по динамическому свойству пересчитывается только вручную.
        if style := self.status_label.style():
            style.unpolish(self.status_label)
            style.polish(self.status_label)
        self.status_label.setText(message)
        self.status_label.show()
        # Скрытая метка не входит в исходный sizeHint. После её появления
        # диалог должен вырасти, иначе Qt сжимает сообщение до тонкой полосы.
        QTimer.singleShot(0, self._fit_status_message)

    def _content_height(self, width: int) -> int:
        """
        Высота содержимого при заданной ширине.

        `sizeHint` считает переносимые строки по минимальной ширине окна, где
        они занимают больше строк, — и под кнопками оставалась пустая полоса.
        """
        self.ensurePolished()
        layout = self.layout()
        if layout is None:
            return self.sizeHint().height()
        layout.activate()
        if layout.hasHeightForWidth():
            return max(self.minimumHeight(), layout.totalHeightForWidth(width))
        return max(self.minimumHeight(), self.sizeHint().height())

    def _fit_status_message(self) -> None:
        """Даёт сообщению об ошибке необходимую высоту без растягивания ширины."""
        required_height = self._content_height(self.width())
        if required_height > self.height():
            self.resize(self.width(), required_height)

    def _save(self) -> None:
        if self._pending_check is not None:
            return

        key = self.key_input.text().strip()
        if not key:
            self._show_error(
                self._t("Введите ключ или выберите «Без ИИ».", "Enter a key or choose Without AI.")
            )
            return
        if not credentials.looks_like_key(key):
            self._show_error(
                self._t(
                    "Ключ выглядит некорректным: проверьте, что скопирован целиком.",
                    "The key looks invalid. Check that it was copied in full.",
                )
            )
            return

        self._start_check(key)

    # --- Проверка ключа ---------------------------------------------------

    def _start_check(self, key: str) -> None:
        """Запускает проверку ключа в фоновом потоке."""
        future: Future[KeyCheckResult] = Future()
        checker = self._key_checker

        def run() -> None:
            try:
                future.set_result(checker(key))
            except BaseException as exc:
                future.set_exception(exc)

        self._pending_check = (key, future)
        self._set_checking(True)
        self._show_status(
            self._t(
                "Проверяем ключ: один короткий запрос к Gemini…",
                "Checking the key with a short Gemini request…",
            ),
            error=False,
        )
        threading.Thread(target=run, name="gemini-key-check", daemon=True).start()
        self._check_timer.start()

    def _poll_check(self) -> None:
        if self._pending_check is None:
            self._check_timer.stop()
            return
        key, future = self._pending_check
        if not future.done():
            return

        self._check_timer.stop()
        self._pending_check = None
        self._set_checking(False)
        try:
            result = future.result()
        except Exception as exc:
            # Текст исключения может содержать ключ — в журнал идёт только тип.
            logger.error("Проверка ключа Gemini не удалась: %s.", type(exc).__name__)
            self._show_error(
                self._t(
                    f"Не удалось проверить ключ ({type(exc).__name__}).",
                    f"Could not check the key ({type(exc).__name__}).",
                )
            )
            return
        self._apply_check_result(key, result)

    def _apply_check_result(self, key: str, result: KeyCheckResult) -> None:
        if not result.ok:
            self._show_error(result.message)
            return
        if result.warning:
            message_dialog.notify(
                self,
                self._t("Ключ сохранён", "Key saved"),
                result.message,
                kind="warning",
                language=self.language,
            )
        self._store_key(key)

    def _cancel_check(self) -> None:
        """Забывает незавершённую проверку: её результат больше никому не нужен."""
        self._check_timer.stop()
        if self._pending_check is not None:
            self._pending_check = None
            self._set_checking(False)

    def _set_checking(self, busy: bool) -> None:
        self.key_input.setEnabled(not busy)
        self.show_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.save_button.setText(
            self._t("Проверяем…", "Checking…") if busy else self._t("Подключить", "Connect")
        )

    def _store_key(self, key: str) -> None:
        try:
            credentials.save_api_key(key)
        except (ValueError, RuntimeError) as exc:
            # Текст ключа в сообщение не попадает.
            self._show_error(self._t(str(exc), "Could not save the key securely."))
            return

        # Клиент мог быть создан со старым ключом.
        from ..core.modules.ai_base import AIBase

        AIBase.reset_client()

        self._offline_chosen = False
        self.accept()

    def _forget(self) -> None:
        if not message_dialog.confirm(
            self,
            self._t("Удалить ключ Gemini?", "Remove the Gemini key?"),
            self._t(
                "Ключ будет удалён с этого компьютера. Приложение продолжит "
                "работать без ИИ, по встроенной базе знаний.",
                "The key will be removed from this computer. The app will keep "
                "working without AI, using its built-in knowledge base.",
            ),
            accept=self._t("Удалить", "Remove"),
            destructive=True,
            kind="warning",
            language=self.language,
        ):
            return

        if not credentials.delete_api_key():
            self._show_error(
                self._t("Не удалось удалить сохранённый ключ.", "Could not remove the saved key.")
            )
            return

        from ..core.modules.ai_base import AIBase

        AIBase.reset_client()

        self._offline_chosen = True
        self.accept()

    def _continue_offline(self) -> None:
        self._offline_chosen = True
        self.accept()

    def done(self, result: int) -> None:
        """
        Закрывает окно любым способом: кнопкой, крестиком или Esc.

        Проверка, начатая до закрытия, отменяется — иначе её результат
        сохранил бы ключ уже после того, как пользователь выбрал «Без ИИ».
        """
        self._cancel_check()
        super().done(result)

    # --- Результат --------------------------------------------------------

    @property
    def offline_chosen(self) -> bool:
        """Выбрал ли пользователь работу без ИИ."""
        return self._offline_chosen
