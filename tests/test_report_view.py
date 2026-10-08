"""Страница отчёта: что показано при разных итогах и куда ведут ссылки."""

import pytest

pytest.importorskip("PyQt6")
pytestmark = [pytest.mark.gui]

from PyQt6.QtWidgets import QPushButton  # noqa: E402

from winspector.core.report_data import DeferredApp, ReportData  # noqa: E402
from winspector.gui.widgets.report_view import ReportView  # noqa: E402

MB = 1024**2


def _view(data, markdown="## Отчёт\n\n- пункт", language="ru"):
    view = ReportView()
    view.show_report(data, markdown, language)
    return view


def _button(view, text):
    return next(b for b in view.widget().findChildren(QPushButton) if b.text() == text)


def test_full_report_shows_every_section(qapp):
    data = ReportData(
        freed_bytes=300 * MB,
        deleted_files=9747,
        deleted_folders=2112,
        skipped_files=5,
        skipped_bytes=MB,
        changes=["Служба обновления Epic Games отключена."],
        deferred=[DeferredApp("Telegram", 200 * MB), DeferredApp("Discord")],
        quarantined_count=3,
        quarantined_bytes=4 * MB,
    )
    text = _view(data).plain_text()
    for expected in (
        "Оптимизация завершена",
        "300,0 МБ",
        "9 747 файлов",
        "2 112",
        "Ждут закрытия программ",
        "Telegram",
        "Discord",
        "Служба обновления Epic Games отключена.",
        "Открыть карантин",
        "Без ИИ",
        "Не тронуто 5 файлов",
    ):
        assert expected in text, expected
    # Внутренние имена правил в окне не показываются.
    assert "chromium_app_caches" not in text


def test_empty_sections_are_hidden(qapp):
    text = _view(ReportData()).plain_text()
    assert "Система уже в порядке" in text
    for hidden in ("Ждут закрытия программ", "Открыть карантин", "Не тронуто"):
        assert hidden not in text


def test_long_deferred_list_is_truncated(qapp):
    apps = [DeferredApp(f"App {index:02}", MB) for index in range(14)]
    text = _view(ReportData(freed_bytes=MB, deferred=apps)).plain_text()
    assert "App 09" in text
    assert "App 10" not in text
    assert "и ещё 4 программы" in text


def test_ai_text_and_failure_states(qapp):
    ai = _view(ReportData(freed_bytes=MB, ai_used=True, ai_text="Совет от **Gemini**"))
    assert "Комментарий Gemini" in ai.plain_text()
    assert "Совет от Gemini" in ai.plain_text()

    failed = _view(ReportData(freed_bytes=MB, ai_failed=True), language="en")
    assert "Gemini didn't respond" in failed.plain_text()
    assert "Gemini settings" in failed.plain_text()


def test_links_emit_signals(qapp):
    view = _view(ReportData(quarantined_count=1, quarantined_bytes=MB))
    opened = []
    view.open_quarantine.connect(lambda: opened.append("quarantine"))
    view.open_gemini_settings.connect(lambda: opened.append("gemini"))
    _button(view, "Открыть карантин").click()
    _button(view, "Подключить Gemini").click()
    assert opened == ["quarantine", "gemini"]


def test_without_data_falls_back_to_markdown(qapp):
    text = _view(None, "## Итог\n\n- Освобождено 1 ГБ").plain_text()
    assert "Отчёт" in text
    assert "Освобождено 1 ГБ" in text


def test_rerender_replaces_previous_report(qapp):
    view = _view(ReportData(freed_bytes=MB))
    view.show_report(ReportData(freed_bytes=MB), "", "en")
    text = view.plain_text()
    assert "Optimization complete" in text
    assert "Оптимизация завершена" not in text
