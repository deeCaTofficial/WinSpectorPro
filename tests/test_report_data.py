"""Итог оптимизации для окна отчёта: названия программ, группировка, числа."""

import pytest

from winspector.core.report_data import (
    ReportData,
    app_name,
    describe_change,
    format_count,
    format_size,
    plural,
)

MB, GB = 1024**2, 1024**3
NBSP = " "


def _deferred(processes=(), folder="", size=0):
    return {"processes": list(processes), "folder": folder, "size_bytes": int(size)}


@pytest.mark.parametrize(
    ("raw", "language", "expected"),
    [
        ("Code.exe", "ru", "Visual Studio Code"),
        ("NVIDIA Overlay.exe", "en", "NVIDIA App"),
        ("Yandex Music.exe", "ru", "Яндекс Музыка"),
        ("YandexMusic", "en", "Yandex Music"),
        ("discord", "ru", "Discord"),
        ("SomeTool.exe", "ru", "SomeTool"),
        ("launch-preview-static", "ru", "launch-preview-static"),
    ],
)
def test_app_name_is_human_readable(raw, language, expected):
    assert app_name(raw, language) == expected


def test_deferred_apps_are_grouped_and_sorted_by_size():
    summary = {
        "cleanup": {
            "deferred": [
                _deferred(["steam.exe"], size=53 * MB),
                _deferred(["Code.exe"], size=91 * MB),
                _deferred(["steam.exe"], size=19 * MB),
                _deferred(["Code.exe"], size=139 * MB),
                _deferred(["Telegram.exe"], size=1 * GB),
                _deferred(folder="Code"),
                _deferred(folder="discord"),
            ]
        }
    }
    data = ReportData.from_summary(summary)
    assert [(app.name, app.size_bytes) for app in data.deferred] == [
        ("Telegram", 1 * GB),
        ("Visual Studio Code", 230 * MB),
        ("Steam", 72 * MB),
        ("Discord", 0),
    ]
    assert data.deferred_bytes == 1 * GB + 302 * MB


def test_shared_deferred_size_is_counted_once():
    summary = {"cleanup": {"deferred": [_deferred(["Discord.exe", "steam.exe"], size=10 * MB)]}}
    data = ReportData.from_summary(summary)
    assert data.deferred_bytes == 10 * MB
    assert {app.name for app in data.deferred} == {"Discord", "Steam"}


def test_from_summary_collects_totals_and_changes():
    summary = {
        "debloat": {
            "completed": [
                {
                    "id": "EpicGamesUpdater",
                    "action": "set_manual",
                    "user_explanation_ru": "Служба Epic.",
                },
                "мусор",
            ],
            "failed": [{"id": "Broken"}],
        },
        "cleanup": {
            "cleaned_size_bytes": 3 * GB,
            "deleted_files_count": 100,
            "deleted_folders_count": 2,
            "skipped_files_count": 5,
            "skipped_size_bytes": 7 * MB,
        },
        "empty_folders": {"deleted_folders_count": 10},
        "leftovers": {"quarantined_count": 3, "quarantined_size_bytes": 4 * MB},
    }
    data = ReportData.from_summary(summary, ai_used=True, ai_text="Текст")
    assert data.freed_bytes == 3 * GB
    assert data.deleted_files == 100
    assert data.deleted_folders == 12
    assert (data.skipped_files, data.skipped_bytes) == (5, 7 * MB)
    assert data.changes == ["Служба Epic."]
    assert data.failed_changes == 1
    assert (data.quarantined_count, data.quarantined_bytes) == (3, 4 * MB)
    assert data.ai_used and data.ai_text == "Текст"
    assert not data.nothing_done

    english = ReportData.from_summary(summary, language="en")
    assert english.changes == ["Service “EpicGamesUpdater” set to start on demand"]


def test_empty_summary_means_nothing_done():
    data = ReportData.from_summary({})
    assert data.nothing_done
    assert data.deferred == []


def test_describe_change_never_shows_raw_action():
    assert describe_change({"id": "DiagTrack", "action": "disable"}, "en") == (
        "Service “DiagTrack” disabled"
    )
    assert (
        describe_change({"id": "DiagTrack", "action": "disable"}) == "Служба «DiagTrack» отключена"
    )
    assert describe_change({"id": "Clipchamp", "action": "remove", "type": "uwp_app"}) == (
        "Приложение «Clipchamp» удалено"
    )


def test_format_size_and_count_follow_language():
    assert format_size(int(1.45 * GB)) == f"1,45{NBSP}ГБ"
    assert format_size(int(1.45 * GB), "en") == f"1.45{NBSP}GB"
    assert format_size(int(235.3 * MB)) == f"235,3{NBSP}МБ"
    assert format_size(512, "en") == f"512{NBSP}bytes"
    assert format_count(9747) == f"9{NBSP}747"
    assert format_count(9747, "en") == "9,747"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, "файл"), (2, "файла"), (5, "файлов"), (11, "файлов"), (21, "файл"), (104, "файла")],
)
def test_plural(value, expected):
    assert plural(value, "файл", "файла", "файлов") == expected


def test_change_uses_windows_display_name():
    summary = {"debloat": {"completed": [{"id": "epicgamesupdater", "action": "set_manual"}]}}
    data = ReportData.from_summary(
        summary, language="en", display_names={"epicgamesupdater": "Epic Games Updater"}
    )
    assert data.changes == ["Service “Epic Games Updater” set to start on demand"]
