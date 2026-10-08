"""
Экран итогов оптимизации.

Раньше здесь показывался Markdown-отчёт целиком: сплошной список, в котором
главное (сколько освобождено) терялось среди внутренних имён правил. Теперь
итог разложен по смыслу: заголовок, три числа, затем карточки — какие
программы мешали очистке и сколько места ждёт их закрытия, что изменено в
системе, что ушло в карантин, — и комментарий Gemini, если он был.

Markdown-версия по-прежнему сохраняется в файл и копируется кнопкой
«Скопировать отчёт».
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextFormat
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core.report_data import ReportData, format_count, format_size, plural
from ..language import localize
from ..theme import (
    ACCENT_TEXT,
    DIM,
    MUTED,
    SUCCESS,
    SUCCESS_SOFT,
    WARNING,
    heading_family,
)
from .glyph_badge import GlyphBadge

GLYPH_DONE = "\ue73e"
GLYPH_WAITING = "\ue823"
GLYPH_CHANGES = "\ue90f"
GLYPH_QUARANTINE = "\ue7b8"
GLYPH_GEMINI = "\ue99a"
GLYPH_INFO = "\ue946"
GLYPH_WARNING = "\ue7ba"
GLYPH_REPORT = "\ue8a5"

# Сколько программ показывать в карточке «Ждут закрытия»; остальные — числом.
_DEFERRED_LIMIT = 10

# Размеры заголовков в тексте Gemini, в пикселях, как и остальные шрифты окна.
_HEADING_PIXELS = {1: 18, 2: 16, 3: 14}
# Отступ списков и цитат: стандартные 40 px уводят их далеко от заголовков.
_INDENT = 22


def style_markdown(browser: QTextBrowser) -> None:
    """
    Подгоняет Markdown под оформление окна.

    Qt даёт заголовкам свои крупные размеры и плотные строки; здесь
    заголовкам задаётся шрифт и размер окна, а абзацам — интервалы, с
    которыми текст удобно читать.
    """
    document = browser.document()
    if document is None:
        return
    document.setIndentWidth(_INDENT)

    def select(start: int, end: int) -> QTextCursor:
        cursor = QTextCursor(document)
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        return cursor

    quote = QTextCharFormat()
    quote.setForeground(QColor(MUTED))

    block = document.begin()
    while block.isValid():
        block_format = block.blockFormat()
        level = block_format.headingLevel()
        block_format.setLineHeight(130, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        block_format.setBottomMargin(2 if block.textList() else 8)
        if level:
            block_format.setTopMargin(12 if block.previous().isValid() else 0)
        if block_format.property(QTextFormat.Property.BlockQuoteLevel):
            # Цитата Markdown по умолчанию сдвинута на 40 px — как отдельная колонка.
            block_format.setLeftMargin(_INDENT)
        QTextCursor(block).setBlockFormat(block_format)

        # Форматы собираются заранее: правка документа сдвигает фрагменты.
        fragments = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            start = fragment.position()
            fragments.append((start, start + fragment.length(), fragment.charFormat()))
            iterator += 1

        for start, end, char_format in fragments:
            if level:
                # Пометку Markdown об относительном размере нужно именно
                # удалить: пока она есть, Qt считает размер от базового
                # шрифта и игнорирует заданный ниже.
                char_format.clearProperty(QTextFormat.Property.FontSizeAdjustment)
                char_format.setFontFamilies([heading_family()])
                char_format.setFontWeight(QFont.Weight.Normal)
                char_format.setProperty(
                    QTextFormat.Property.FontPixelSize, _HEADING_PIXELS.get(level, 14)
                )
            # Qt читает `_текст_` как подчёркивание, и такой текст похож на ссылку.
            if char_format.fontUnderline() and not char_format.isAnchor():
                char_format.setFontUnderline(False)
                char_format.setFontItalic(True)
            select(start, end).setCharFormat(char_format)

        if block_format.property(QTextFormat.Property.BlockQuoteLevel):
            select(block.position(), block.position() + block.length() - 1).mergeCharFormat(quote)
        block = block.next()


class _FittedText(QTextBrowser):
    """Текст Markdown во всю высоту: листается вся страница, а не окошко внутри неё."""

    def __init__(self, markdown: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReportText")
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # Клавиши листают страницу целиком, поэтому фокус тексту не нужен.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        document = self.document()
        assert document is not None
        document.setDocumentMargin(0)
        self.setMarkdown(markdown)
        style_markdown(self)
        self._fit()

    def _fit(self) -> None:
        document = self.document()
        if document is None:
            return
        document.setTextWidth(self.viewport().width())
        self.setFixedHeight(round(document.size().height()) + 2)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit()

    def wheelEvent(self, event) -> None:
        # Колесо листает страницу: своей прокрутки у текста нет.
        event.ignore()


def _label(text: str, name: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class ReportView(QScrollArea):
    """Итоги оптимизации: числа и карточки вместо сплошного текста."""

    open_quarantine = pyqtSignal()
    open_gemini_settings = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ReportScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Страница листается клавишами, и ни одна кнопка не в рамке фокуса.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._language = "ru"

    def _t(self, russian: str, english: str) -> str:
        return localize(self._language, russian, english)

    # --- Построение ----------------------------------------------------------

    def show_report(self, data: ReportData | None, markdown: str, language: str) -> None:
        """Показывает итог; без данных по полям — просто текст отчёта."""
        self._language = language
        content = QWidget()
        content.setObjectName("ReportContent")
        layout = QVBoxLayout(content)
        # Справа место под полосу прокрутки, чтобы она не прилипала к карточкам.
        layout.setContentsMargins(0, 4, 14, 8)
        layout.setSpacing(12)

        if data is None:
            self._add_hero(layout, nothing_done=False)
            card, body = self._card(GLYPH_REPORT, ACCENT_TEXT, self._t("Отчёт", "Report"))
            body.addWidget(_FittedText(markdown))
            layout.addWidget(card)
        else:
            self._add_hero(layout, nothing_done=data.nothing_done)
            self._add_tiles(layout, data)
            if data.deferred:
                self._add_deferred(layout, data)
            if data.changes or data.failed_changes:
                self._add_changes(layout, data)
            if data.quarantined_count:
                self._add_quarantine(layout, data)
            self._add_gemini(layout, data)
            if data.skipped_files:
                self._add_skipped_note(layout, data)
        layout.addStretch()

        self.setWidget(content)
        self.verticalScrollBar().setValue(0)

    def plain_text(self) -> str:
        """Весь видимый текст страницы — для тестов и проверки снимков."""
        content = self.widget()
        if content is None:
            return ""
        parts = []
        for child in content.findChildren(QWidget):
            if isinstance(child, QLabel | QPushButton):
                parts.append(child.text())
            elif isinstance(child, QTextBrowser):
                parts.append(child.toPlainText())
        return "\n".join(parts)

    def _add_hero(self, layout: QVBoxLayout, *, nothing_done: bool) -> None:
        row = QHBoxLayout()
        row.setSpacing(16)
        row.setContentsMargins(0, 0, 0, 8)
        badge = GlyphBadge(GLYPH_DONE, SUCCESS, SUCCESS_SOFT, size=48, glyph_size=22, radius=14)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        if nothing_done:
            title = self._t("Система уже в порядке", "Your system is already in good shape")
            subtitle = self._t(
                "Удалять и менять было нечего. Точка восстановления создана на всякий случай.",
                "There was nothing to remove or change. A restore point was created just in case.",
            )
        else:
            title = self._t("Оптимизация завершена", "Optimization complete")
            subtitle = self._t(
                "Перед изменениями создана точка восстановления — всё можно откатить.",
                "A restore point was created before any change, so everything can be rolled back.",
            )
        texts.addWidget(_label(title, "ReportTitle", wrap=True))
        texts.addWidget(_label(subtitle, "CardText", wrap=True))
        row.addLayout(texts, 1)
        layout.addLayout(row)

    def _add_tiles(self, layout: QVBoxLayout, data: ReportData) -> None:
        language = self._language
        files = data.deleted_files
        failed = data.failed_changes
        tiles = [
            (
                self._t("Освобождено", "Freed"),
                format_size(data.freed_bytes, language),
                self._t(
                    f"{format_count(files, language)} {plural(files, 'файл', 'файла', 'файлов')}",
                    f"{format_count(files, language)} {'file' if files == 1 else 'files'}",
                ),
            ),
            (
                self._t("Пустые папки", "Empty folders"),
                format_count(data.deleted_folders, language),
                self._t("удалены", "removed"),
            ),
            (
                self._t("Изменения в системе", "System changes"),
                format_count(len(data.changes), language),
                self._t(f"не удалось: {failed}", f"failed: {failed}")
                if failed
                else self._t("службы и настройки", "services and settings"),
            ),
        ]
        row = QHBoxLayout()
        row.setSpacing(12)
        for label, value, caption in tiles:
            tile = QFrame()
            tile.setObjectName("ReportTile")
            box = QVBoxLayout(tile)
            box.setContentsMargins(16, 14, 16, 14)
            box.setSpacing(2)
            box.addWidget(_label(label, "TileLabel"))
            box.addWidget(_label(value, "TileValue"))
            box.addWidget(_label(caption, "TileCaption"))
            row.addWidget(tile, 1)
        layout.addLayout(row)

    def _card(
        self, glyph: str, color: str, title: str, meta: str = ""
    ) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("ReportCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(20, 16, 20, 18)
        box.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(GlyphBadge(glyph, color, size=20, glyph_size=16))
        header.addWidget(_label(title, "CardTitle"))
        header.addStretch()
        if meta:
            header.addWidget(_label(meta, "CardMeta"))
        box.addLayout(header)
        return card, box

    def _add_deferred(self, layout: QVBoxLayout, data: ReportData) -> None:
        language = self._language
        meta = f"≈ {format_size(data.deferred_bytes, language)}" if data.deferred_bytes else ""
        card, body = self._card(
            GLYPH_WAITING,
            WARNING,
            self._t("Ждут закрытия программ", "Waiting for apps to close"),
            meta,
        )
        body.addWidget(
            _label(
                self._t(
                    "Эти программы были запущены, поэтому их кеш не трогали. Закройте их и "
                    "запустите оптимизацию ещё раз — освободится больше места.",
                    "These apps were running, so their caches were left alone. Close them and "
                    "run the optimization again to free more space.",
                ),
                "CardText",
                wrap=True,
            )
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(32)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(0, 4, 0, 0)
        shown = data.deferred[:_DEFERRED_LIMIT]
        rows = (len(shown) + 1) // 2
        for index, app in enumerate(shown):
            cell = QHBoxLayout()
            cell.setSpacing(12)
            cell.addWidget(_label(app.name, "ReportRowName"))
            cell.addStretch()
            if app.size_bytes:
                cell.addWidget(_label(format_size(app.size_bytes, language), "ReportRowValue"))
            # Заполняем по столбцам: крупные программы — в начале левого.
            grid.addLayout(cell, index % rows, index // rows)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        body.addLayout(grid)
        hidden = len(data.deferred) - len(shown)
        if hidden:
            body.addWidget(
                _label(
                    self._t(
                        f"и ещё {hidden} {plural(hidden, 'программа', 'программы', 'программ')}",
                        f"and {hidden} more",
                    ),
                    "CardText",
                )
            )
        layout.addWidget(card)

    def _add_changes(self, layout: QVBoxLayout, data: ReportData) -> None:
        card, body = self._card(
            GLYPH_CHANGES, ACCENT_TEXT, self._t("Изменения в системе", "System changes")
        )
        rows = [(GLYPH_DONE, SUCCESS, text) for text in data.changes]
        if data.failed_changes:
            rows.append(
                (
                    GLYPH_WARNING,
                    WARNING,
                    self._t(
                        f"Не удалось изменить: {data.failed_changes}. "
                        "Подробности — в журнале winspector.log.",
                        f"Could not change: {data.failed_changes}. Details are in winspector.log.",
                    ),
                )
            )
        for glyph, color, text in rows:
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(
                GlyphBadge(glyph, color, size=20, glyph_size=14), 0, Qt.AlignmentFlag.AlignTop
            )
            row.addWidget(_label(text, "ReportRowName", wrap=True), 1)
            body.addLayout(row)
        layout.addWidget(card)

    def _add_quarantine(self, layout: QVBoxLayout, data: ReportData) -> None:
        count = data.quarantined_count
        size = format_size(data.quarantined_bytes, self._language)
        card, body = self._card(
            GLYPH_QUARANTINE,
            ACCENT_TEXT,
            self._t("Остатки удалённых программ", "Leftovers of removed apps"),
        )
        body.addWidget(
            _label(
                self._t(
                    f"{count} {plural(count, 'папка перенесена', 'папки перенесены', 'папок перенесено')}"
                    f" в карантин ({size}), а не удалены. Вернуть их можно в течение 30 дней.",
                    f"{count} {'folder was' if count == 1 else 'folders were'} moved to quarantine "
                    f"({size}), not deleted. You can restore them for 30 days.",
                ),
                "CardText",
                wrap=True,
            )
        )
        button = QPushButton(self._t("Открыть карантин", "Open quarantine"))
        button.setObjectName("LinkButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.open_quarantine)
        body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)

    def _add_gemini(self, layout: QVBoxLayout, data: ReportData) -> None:
        if data.ai_used:
            card, body = self._card(
                GLYPH_GEMINI, ACCENT_TEXT, self._t("Комментарий Gemini", "Gemini's notes")
            )
            body.addWidget(_FittedText(data.ai_text))
            layout.addWidget(card)
            return
        if data.ai_failed:
            title = self._t("Gemini не ответил", "Gemini didn't respond")
            text = self._t(
                "План и отчёт собраны по встроенной базе знаний. Проверьте интернет или ключ.",
                "The plan and report were built from the built-in knowledge base. "
                "Check your connection or key.",
            )
            action = self._t("Настройки Gemini", "Gemini settings")
        else:
            title = self._t("Без ИИ", "Without AI")
            text = self._t(
                "Изменения подобраны по встроенной базе знаний — осторожно и одинаково для всех. "
                "С ключом Gemini план учтёт, как вы пользуетесь компьютером.",
                "Changes were picked from the built-in knowledge base — carefully and the same "
                "for everyone. With a Gemini key, the plan adapts to how you use your PC.",
            )
            action = self._t("Подключить Gemini", "Connect Gemini")
        card, body = self._card(GLYPH_GEMINI, DIM, title)
        body.addWidget(_label(text, "CardText", wrap=True))
        button = QPushButton(action)
        button.setObjectName("LinkButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.open_gemini_settings)
        body.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(card)

    def _add_skipped_note(self, layout: QVBoxLayout, data: ReportData) -> None:
        language = self._language
        files = data.skipped_files
        size = format_size(data.skipped_bytes, language)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(4, 2, 0, 0)
        row.addWidget(
            GlyphBadge(GLYPH_INFO, DIM, size=16, glyph_size=12), 0, Qt.AlignmentFlag.AlignTop
        )
        row.addWidget(
            _label(
                self._t(
                    f"Не тронуто {format_count(files, language)} "
                    f"{plural(files, 'файл', 'файла', 'файлов')} ({size}): они заняты программами "
                    "или созданы за последние сутки.",
                    f"Left alone: {format_count(files, language)} "
                    f"{'file' if files == 1 else 'files'} ({size}) — in use or created in the "
                    "last 24 hours.",
                ),
                "ReportNote",
                wrap=True,
            ),
            1,
        )
        layout.addLayout(row)
