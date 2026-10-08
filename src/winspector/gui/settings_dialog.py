"""Настройки и точки входа к существующим данным приложения."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_VERSION, COPYRIGHT
from ..core import credentials
from ..core.modules import quarantine
from ..core.modules.ai_base import configured_model
from ..core.update_checker import LATEST_RELEASE_PAGE, UpdateInfo
from . import message_dialog
from .api_key_dialog import ApiKeyDialog
from .language import get_language, localize, set_language
from .theme import MUTED, SUCCESS
from .widgets.setting_row import row_icon, set_row_icon, setting_row

# Значки строк — из системного шрифта значков Windows.
_GLYPH_LANGUAGE = "\ue774"
_GLYPH_UPDATES = "\ue895"
_GLYPH_REPORTS = "\ue8b7"
_GLYPH_GEMINI = "\ue99a"
_GLYPH_LOCK = "\ue72e"
_GLYPH_QUARANTINE = "\ue7b8"


def reports_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    return (Path(base) if base else Path.home()) / "WinSpectorPro" / "Reports"


class SettingsDialog(QDialog):
    credentials_changed = pyqtSignal()
    check_updates_requested = pyqtSignal()
    language_changed = pyqtSignal(str)

    def __init__(
        self,
        parent=None,
        *,
        initial_tab: int = 0,
        update_info: UpdateInfo | None = None,
        update_checking: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setMinimumSize(560, 460)
        self.resize(620, 500)
        self.setModal(True)
        self._restore_task: asyncio.Task | None = None
        self._update_info = update_info
        self._update_checking = update_checking
        self.language = get_language()

        # Заголовок «Настройки» уже есть в рамке окна, поэтому сразу вкладки.
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 12, 24, 20)
        root.setSpacing(0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("SettingsTabs")
        if tab_bar := self.tabs.tabBar():
            tab_bar.setDrawBase(False)
        self.tabs.addTab(self._general_tab(), "")
        self.tabs.addTab(self._gemini_tab(), "Gemini")
        self.tabs.addTab(self._quarantine_tab(), "")
        self.tabs.setCurrentIndex(initial_tab)
        root.addWidget(self.tabs, 1)
        root.addSpacing(16)

        footer = QHBoxLayout()
        self.about = self._label("Caption")
        self.about.setText(f"{APP_NAME} {APP_VERSION}")
        footer.addWidget(self.about, 0, Qt.AlignmentFlag.AlignVCenter)
        footer.addStretch()
        self.close_button = QPushButton()
        self.close_button.setMinimumWidth(110)
        self.close_button.clicked.connect(self.accept)
        footer.addWidget(self.close_button)
        root.addLayout(footer)
        self._retranslate()

    def _t(self, russian: str, english: str) -> str:
        return localize(self.language, russian, english)

    def _change_language(self, index: int) -> None:
        language = self.language_combo.itemData(index)
        if language == self.language:
            return
        try:
            set_language(language)
        except OSError:
            self.language_combo.blockSignals(True)
            self.language_combo.setCurrentIndex(0 if self.language == "ru" else 1)
            self.language_combo.blockSignals(False)
            message_dialog.notify(
                self,
                self._t("Не удалось сохранить язык", "Could not save language"),
                self._t(
                    "Проверьте доступ к папке настроек приложения.",
                    "Check access to the application settings folder.",
                ),
                kind="warning",
                language=self.language,
            )
            return
        self.language = language
        self._retranslate()
        self.language_changed.emit(language)

    def _retranslate(self) -> None:
        self.setWindowTitle(self._t("Настройки — WinSpector Pro", "Settings — WinSpector Pro"))
        self.tabs.setTabText(0, self._t("Общие", "General"))
        self.tabs.setTabText(2, self._t("Карантин", "Quarantine"))
        self.close_button.setText(self._t("Закрыть", "Close"))
        self.about.setToolTip(self._t(f"{COPYRIGHT} · Лицензия MIT", f"{COPYRIGHT} · MIT License"))

        self.language_title.setText(self._t("Язык", "Language"))
        self.language_subtitle.setText(self._t("Интерфейс и отчёты", "Interface and reports"))
        self.update_title.setText(self._t("Обновления", "Updates"))
        self.reports_title.setText(self._t("Отчёты", "Reports"))
        self.reports_subtitle.setText(
            self._t("Хранятся на этом компьютере", "Kept on this computer")
        )
        self.open_reports_button.setText(self._t("Открыть", "Open"))

        self.key_title.setText(self._t("Ключ зашифрован", "Key is encrypted"))
        if credentials.looks_like_key(os.environ.get("GEMINI_API_KEY")):
            self.gemini_note.setText(
                self._t(
                    "Сейчас действует ключ из переменной GEMINI_API_KEY",
                    "The GEMINI_API_KEY environment variable is in use",
                )
            )
        else:
            self.gemini_note.setText(
                self._t(
                    "Хранится только на этом компьютере (Windows DPAPI)",
                    "Stored only on this computer (Windows DPAPI)",
                )
            )
        self.delete_key_button.setText(self._t("Удалить", "Remove"))

        self.quarantine_title.setText(self._t("Карантин", "Quarantine"))
        self.quarantine_note.setText(
            self._t("Остатки программ хранятся 30 дней", "Program leftovers are kept for 30 days")
        )
        self.quarantine_empty.setText(
            self._t(
                "Здесь пока пусто.\nОстатки удалённых программ появятся после оптимизации.",
                "Nothing here yet.\nLeftovers of uninstalled programs appear after an optimization.",
            )
        )
        self.open_quarantine_button.setText(self._t("Открыть папку", "Open folder"))
        self.restore_button.setText(self._t("Восстановить выбранное", "Restore selected"))
        self.batches_title.setText(self._t("Перенесено в карантин", "Moved to quarantine"))
        self._refresh_key()
        self.set_update_state(self._update_info, self._update_checking)

    @staticmethod
    def _tab() -> tuple[QWidget, QVBoxLayout]:
        widget = QWidget()
        widget.setObjectName("SettingsPage")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 18, 0, 0)
        layout.setSpacing(6)
        return widget, layout

    @staticmethod
    def _label(name: str, *, wrap: bool = False) -> QLabel:
        label = QLabel()
        label.setObjectName(name)
        label.setWordWrap(wrap)
        return label

    def _general_tab(self) -> QWidget:
        widget, layout = self._tab()

        self.language_combo = QComboBox()
        self.language_combo.addItem("Русский", "ru")
        self.language_combo.addItem("English", "en")
        self.language_combo.setCurrentIndex(0 if self.language == "ru" else 1)
        self.language_combo.currentIndexChanged.connect(self._change_language)
        row, self.language_title, self.language_subtitle = setting_row(
            _GLYPH_LANGUAGE, self.language_combo
        )
        layout.addWidget(row)

        self.check_updates_button = QPushButton()
        self.check_updates_button.clicked.connect(lambda: self.check_updates_requested.emit())
        self.open_release_button = QPushButton()
        self.open_release_button.setObjectName("PrimaryButton")
        self.open_release_button.clicked.connect(self._open_latest_release)
        row, self.update_title, self.update_status_label = setting_row(
            _GLYPH_UPDATES, self.check_updates_button, self.open_release_button
        )
        layout.addWidget(row)

        self.open_reports_button = QPushButton()
        self.open_reports_button.clicked.connect(self._open_reports)
        row, self.reports_title, self.reports_subtitle = setting_row(
            _GLYPH_REPORTS, self.open_reports_button
        )
        layout.addWidget(row)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        return scroll

    def set_update_state(self, info: UpdateInfo | None, checking: bool) -> None:
        """Показывает результат общей для окна и настроек проверки — одной строкой."""
        self._update_info = info
        self._update_checking = checking
        available = info is not None and info.available and not checking
        details = ""
        if checking:
            text = self._t("Проверяем GitHub…", "Checking GitHub…")
        elif info is None:
            text = self._t("Ещё не проверялось", "Not checked yet")
        elif info.error:
            errors = {
                "Не удалось определить установленную версию.": "Could not determine the installed version.",
                "GitHub вернул релиз без распознаваемой версии.": "GitHub returned a release with an unrecognized version.",
                "Не удалось проверить обновления на GitHub.": "Could not check GitHub for updates.",
            }
            text = self._t("Не удалось проверить", "Could not check")
            details = self._t(info.error, errors.get(info.error, "Could not check for updates."))
        elif info.available:
            text = self._t(
                f"Доступна версия {info.latest_version}",
                f"Version {info.latest_version} is available",
            )
        else:
            text = self._t("У вас последняя версия", "You have the latest version")
        self.update_status_label.setText(text)
        self.update_status_label.setToolTip(details)

        # Одно действие за раз: либо проверить, либо скачать найденное.
        self.check_updates_button.setVisible(not available)
        self.check_updates_button.setEnabled(not checking)
        self.check_updates_button.setText(
            self._t("Проверка…", "Checking…") if checking else self._t("Проверить", "Check")
        )
        self.open_release_button.setVisible(available)
        if available:
            self.open_release_button.setText(self._t("Скачать  ↗", "Download  ↗"))

    def _open_latest_release(self) -> None:
        if not QDesktopServices.openUrl(QUrl(LATEST_RELEASE_PAGE)):
            message_dialog.notify(
                self,
                self._t("GitHub недоступен", "GitHub unavailable"),
                self._t("Не удалось открыть страницу релиза.", "Could not open the release page."),
                kind="warning",
                language=self.language,
            )

    def _gemini_tab(self) -> QWidget:
        widget, layout = self._tab()
        self.delete_key_button = QPushButton()
        self.delete_key_button.setObjectName("DangerButton")
        self.delete_key_button.clicked.connect(self._delete_key)
        self.edit_key_button = QPushButton()
        self.edit_key_button.clicked.connect(self._edit_key)
        self.gemini_row, self.gemini_status, self.gemini_model = setting_row(
            _GLYPH_GEMINI, self.delete_key_button, self.edit_key_button
        )
        layout.addWidget(self.gemini_row)
        row, self.key_title, self.gemini_note = setting_row(_GLYPH_LOCK)
        layout.addWidget(row)
        layout.addStretch()
        self._refresh_key()
        return widget

    def _refresh_key(self) -> None:
        connected = credentials.has_api_key()
        self.gemini_status.setText(
            self._t("Gemini подключён", "Gemini connected")
            if connected
            else self._t("Gemini не подключён", "Gemini not connected")
        )
        self.gemini_model.setText(
            self._t(f"Модель {configured_model()}", f"Model {configured_model()}")
            if connected
            else self._t("Сейчас работает без ИИ", "Currently works without AI")
        )
        # Значок Gemini зелёный, пока ИИ подключён, — как точка на главном экране.
        if icon := row_icon(self.gemini_row):
            set_row_icon(icon, _GLYPH_GEMINI, SUCCESS if connected else MUTED)
        self.edit_key_button.setText(
            self._t("Заменить ключ", "Replace key")
            if connected
            else self._t("Добавить ключ", "Add key")
        )
        # Пока ключа нет, главное действие вкладки — добавить его.
        self.edit_key_button.setObjectName("" if connected else "PrimaryButton")
        if style := self.edit_key_button.style():
            style.unpolish(self.edit_key_button)
            style.polish(self.edit_key_button)
        stored = credentials.storage_path().is_file()
        self.delete_key_button.setEnabled(stored)
        self.delete_key_button.setVisible(stored)
        self.edit_key_button.setEnabled(
            not credentials.looks_like_key(os.environ.get("GEMINI_API_KEY"))
        )

    def _edit_key(self) -> None:
        ApiKeyDialog(self, first_run=False, language=self.language).exec()
        self._refresh_key()
        self.credentials_changed.emit()

    def _delete_key(self) -> None:
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
            message_dialog.notify(
                self,
                self._t("Не удалось удалить ключ", "Could not remove key"),
                self._t("Сохранённый ключ не удалён.", "The saved key was not removed."),
                kind="error",
                language=self.language,
            )
            return
        from ..core.modules.ai_base import AIBase

        AIBase.reset_client()
        self._refresh_key()
        self.credentials_changed.emit()

    def _quarantine_tab(self) -> QWidget:
        widget, layout = self._tab()
        self.open_quarantine_button = QPushButton()
        self.open_quarantine_button.clicked.connect(self._open_quarantine)
        row, self.quarantine_title, self.quarantine_note = setting_row(
            _GLYPH_QUARANTINE, self.open_quarantine_button
        )
        layout.addWidget(row)
        layout.addSpacing(14)

        # Заголовок списка с действием над выбранной партией: кнопка рядом
        # со списком, а не второй строкой над «Закрыть».
        header = QHBoxLayout()
        self.batches_title = self._label("FieldLabel")
        header.addWidget(self.batches_title, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch()
        self.restore_button = QPushButton()
        self.restore_button.clicked.connect(self._restore_selected)
        header.addWidget(self.restore_button)
        layout.addLayout(header)
        layout.addSpacing(2)
        self.batches = QListWidget()
        self.batches.itemSelectionChanged.connect(self._update_restore_button)
        # Вместо пустой рамки — пояснение, откуда здесь что-то возьмётся.
        self.quarantine_empty = self._label("EmptyState", wrap=True)
        self.quarantine_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.batches, 1)
        layout.addWidget(self.quarantine_empty, 1)
        self._refresh_batches()
        return widget

    def _refresh_batches(self) -> None:
        self.batches.clear()
        for batch in quarantine.list_batches():
            created = quarantine.batch_created(batch)
            item = QListWidgetItem(f"{created:%d.%m.%Y  %H:%M}" if created else batch.name)
            item.setData(Qt.ItemDataRole.UserRole, str(batch))
            self.batches.addItem(item)
        empty = self.batches.count() == 0
        self.batches.setVisible(not empty)
        self.quarantine_empty.setVisible(empty)
        self._update_restore_button()

    def _update_restore_button(self) -> None:
        """Восстанавливать можно только выбранную партию и не во время другого восстановления."""
        self.restore_button.setEnabled(
            bool(self.batches.selectedItems()) and not self._restore_in_progress()
        )

    def _restore_selected(self) -> None:
        item = self.batches.currentItem()
        if item is None:
            return
        batch = Path(item.data(Qt.ItemDataRole.UserRole))
        if not message_dialog.confirm(
            self,
            self._t("Вернуть остатки на место?", "Restore the leftovers?"),
            self._t(
                f"Файлы, перенесённые в карантин {item.text()}, вернутся в исходные папки.",
                f"Files moved to quarantine on {item.text()} will go back to their "
                "original folders.",
            ),
            accept=self._t("Вернуть", "Restore"),
            language=self.language,
        ):
            return
        self.restore_button.setEnabled(False)
        self._restore_task = asyncio.create_task(self._restore(batch))

    async def _restore(self, batch: Path) -> None:
        try:
            count, errors = await asyncio.to_thread(quarantine.restore_batch, batch)
            message = self._t(f"Восстановлено элементов: {count}.", f"Items restored: {count}.")
            if errors:
                message += self._t(
                    "\n\nНе удалось восстановить:\n", "\n\nCould not restore:\n"
                ) + "\n".join(f"{Path(path).name}: {reason}" for path, reason in errors.items())
            message_dialog.notify(
                self,
                self._t("Остатки возвращены", "Leftovers restored")
                if not errors
                else self._t("Возвращено не всё", "Not everything was restored"),
                message,
                kind="warning" if errors else "info",
                language=self.language,
            )
        except Exception as exc:
            message_dialog.notify(
                self,
                self._t("Восстановление не удалось", "Restore failed"),
                str(exc),
                kind="error",
                language=self.language,
            )
        finally:
            self._refresh_batches()

    def _restore_in_progress(self) -> bool:
        return self._restore_task is not None and not self._restore_task.done()

    def accept(self) -> None:
        if not self._restore_in_progress():
            super().accept()

    def reject(self) -> None:
        if not self._restore_in_progress():
            super().reject()

    def closeEvent(self, event) -> None:
        if self._restore_in_progress():
            event.ignore()
        else:
            super().closeEvent(event)

    def _open_reports(self) -> None:
        self._open_folder(reports_path())

    def _open_quarantine(self) -> None:
        self._open_folder(quarantine.quarantine_root())

    def _open_folder(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            message_dialog.notify(
                self,
                self._t("Папка недоступна", "Folder unavailable"),
                str(exc),
                kind="warning",
                language=self.language,
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            message_dialog.notify(
                self,
                self._t("Папка недоступна", "Folder unavailable"),
                self._t(f"Не удалось открыть {path}", f"Could not open {path}"),
                kind="warning",
                language=self.language,
            )
