"""
Снимает скриншоты и демо-GIF настоящего интерфейса WinSpector Pro для README.

Ничего не рисуется и не подменяется: окно строится тем же кодом, что и в
приложении, а кадры снимаются с живых виджетов через `QWidget.grab()`.

Режимы:
    --static  главный экран и окно подключения Gemini (01, 02).
              Права администратора не нужны, система не меняется.
    --run     настоящий запуск оптимизации: демо-GIF и экраны 03–05.
              Нужны права администратора (скрипт попросит их сам), и программа
              ВЫПОЛНИТ оптимизацию этого компьютера: создаст точку
              восстановления, изменит службы, очистит мусор, перенесёт остатки
              программ в карантин. Лучше запускать на тестовой виртуальной машине.

--offscreen рисует окно в памяти, не показывая его на экране: кадры те же,
а съёмка не мешает работать (или играть) на этом компьютере.

--language ru|en выбирает язык интерфейса только для съёмки: выбор
пользователя в настройках программы не читается и не меняется.

Результат: docs/media/screenshots/<язык>/*.png и docs/media/demo-<язык>.gif.
Что должно быть на каждом снимке и как проверить их перед публикацией — в
docs/media/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import functools
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from src.winspector.application import Application  # noqa: E402
from src.winspector.core import credentials  # noqa: E402
from src.winspector.gui.api_key_dialog import ApiKeyDialog  # noqa: E402

MEDIA_DIR = PROJECT_ROOT / "docs" / "media"
LANGUAGES = ("ru", "en")

# Строки состояния, по которым снимаются этапы 03 и 04. Русский интерфейс
# показывает текст ядра, английский — свои фразы по проценту выполнения
# (`MainWindow._update_progress`); оба появляются на одних и тех же шагах.
STAGE_MARKERS = {
    "ru": {
        "03-analysis.png": "Анализ вашего стиля",
        "04-optimization.png": "Применение оптимизаций",
    },
    "en": {"03-analysis.png": "Analyzing the system", "04-optimization.png": "Optimizing"},
}

# GIF: ширина и частота кадров из docs/media/README.md.
GIF_WIDTH = 760
GIF_FPS = 10
# Длинное ожидание (анализ, очистка) ускоряется, а не вырезается: этап
# обработки сжимается до этой длительности, начало и итог идут в реальном времени.
PROCESSING_BUDGET_S = 30
# Во время обработки кадр снимается реже: за несколько минут работы иначе
# накопились бы гигабайты кадров, а в GIF они всё равно пойдут с ускорением.
PROCESSING_CAPTURE_FPS = 3
RUN_TIMEOUT_S = 30 * 60


def app_paths() -> dict[str, Path]:
    """Пути режима разработки — те же, что строит `src/main.py`."""
    src = PROJECT_ROOT / "src"
    return {
        "base": src,
        "logs": PROJECT_ROOT / "logs",
        "assets": PROJECT_ROOT / "assets",
        "kb_path": src / "winspector" / "data" / "knowledge_base",
    }


def shots_dir(language: str) -> Path:
    return MEDIA_DIR / "screenshots" / language


def gif_path(language: str) -> Path:
    return MEDIA_DIR / f"demo-{language}.gif"


def force_language(language: str) -> None:
    """
    Язык интерфейса только для этой съёмки.

    Окна узнают язык через `get_language()`, которая читает и при первом
    запуске записывает settings.ini пользователя. Здесь она подменяется во
    всех модулях программы, поэтому файл настроек остаётся нетронутым.
    """
    for name, module in list(sys.modules.items()):
        if name.startswith("src.winspector") and hasattr(module, "get_language"):
            setattr(module, "get_language", lambda: language)  # noqa: B010


def build_application() -> Application:
    """
    Готовит приложение так же, как `Application.initialize`, но без проверки
    прав, без защиты от второго экземпляра и без окна ключа: окно ключа
    снимается отдельно, а запуск с ИИ или без него определяется сохранённым
    ключом.
    """
    app = Application(app_paths())
    app._setup_logging()
    app.q_app = QApplication(sys.argv)
    app._apply_styles()
    app._set_app_icon()
    app.ai_enabled = credentials.has_api_key()
    if not app._initialize_core():
        raise SystemExit("Не удалось подготовить ядро приложения — см. logs/winspector.log.")
    app._initialize_gui()
    return app


def run_scenario(app: Application, scenario, *, offscreen: bool = False) -> None:
    loop = app._setup_async_loop()
    window = app.main_window
    assert window is not None, "главное окно создаётся в build_application"
    if offscreen:
        # `grab()` рисует виджет сам, поэтому кадры не зависят от того, виден ли он.
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.move(80, 60)
    window.show()
    with loop:
        loop.run_until_complete(scenario(window))
        window.close()
        loop.run_until_complete(app._shutdown())


def save_shot(widget: QWidget, directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    if not widget.grab().save(str(path), "PNG"):
        raise RuntimeError(f"Не удалось сохранить {path}")
    print(f"  снимок: {path.relative_to(PROJECT_ROOT)}")
    return path


# --- Статичные снимки ------------------------------------------------------------


async def static_scenario(window, language: str) -> None:
    directory = shots_dir(language)
    # Даём окну дорисоваться и подтянуть шрифты.
    await asyncio.sleep(2.5)
    save_shot(window, directory, "01-main-screen.png")

    # Окно ключа в том виде, в каком его открывает «Настройки → Gemini».
    dialog = ApiKeyDialog(window, first_run=False, language=language)
    if window.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen):
        dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dialog.show()
    await asyncio.sleep(1.0)
    save_shot(dialog, directory, "02-gemini-key.png")
    dialog.done(0)


# --- Запись настоящего запуска ---------------------------------------------------


class Recorder:
    """Пишет кадры окна во временный каталог, помечая этап сценария."""

    def __init__(self, widget: QWidget, frames_dir: Path) -> None:
        self.widget = widget
        self.frames_dir = frames_dir
        self.frames: list[tuple[str, Path]] = []
        self.phase = "home"
        self._timer = QTimer()
        self._timer.timeout.connect(self._capture)
        self.set_phase("home")

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        fps = PROCESSING_CAPTURE_FPS if phase == "processing" else GIF_FPS
        self._timer.setInterval(1000 // fps)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _capture(self) -> None:
        image = self.widget.grab().toImage()
        image = image.scaledToWidth(GIF_WIDTH, Qt.TransformationMode.SmoothTransformation)
        path = self.frames_dir / f"{len(self.frames):05d}.png"
        image.save(str(path), "PNG")
        self.frames.append((self.phase, path))


def compose_gif(frames: list[tuple[str, Path]], target: Path) -> None:
    """
    Собирает GIF: начало и итог — в реальном времени, обработка — ускоренно.

    Кадры обработки сняты с частотой PROCESSING_CAPTURE_FPS и воспроизводятся
    с GIF_FPS; если и так выходит дольше бюджета, берётся каждый n-й кадр.
    """
    from PIL import Image

    processing = [path for phase, path in frames if phase == "processing"]
    budget = PROCESSING_BUDGET_S * GIF_FPS
    step = max(1, -(-len(processing) // budget))
    keep_processing = set(processing[::step])
    selected = [path for phase, path in frames if phase != "processing" or path in keep_processing]

    # Одна палитра на весь ролик: без неё цвета фона «мигают» от кадра к кадру.
    reference = Image.open(selected[len(selected) // 2]).convert("RGB")
    palette = reference.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    images = [
        Image.open(path).convert("RGB").quantize(palette=palette, dither=Image.Dither.NONE)
        for path in selected
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        target,
        save_all=True,
        append_images=images[1:],
        duration=1000 // GIF_FPS,
        loop=0,
        optimize=True,
    )
    seconds = len(images) / GIF_FPS
    size_mb = target.stat().st_size / 1024 / 1024
    print(
        f"  GIF: {target.relative_to(PROJECT_ROOT)} — {len(images)} кадров, "
        f"{seconds:.0f} с, {size_mb:.1f} МБ (обработка ускорена в "
        f"{step * GIF_FPS / PROCESSING_CAPTURE_FPS:.0f} раз)"
    )


def make_run_scenario(frames_dir: Path, language: str):
    directory = shots_dir(language)

    async def scenario(window) -> None:
        recorder = Recorder(window, frames_dir)
        recorder.start()
        await asyncio.sleep(3)

        recorder.set_phase("processing")
        window.start_button.click()
        wanted = dict(STAGE_MARKERS[language])
        deadline = time.monotonic() + RUN_TIMEOUT_S
        while window.is_optimizing and time.monotonic() < deadline:
            status = window.status_label.text()
            for name, marker in list(wanted.items()):
                if marker in status:
                    save_shot(window, directory, name)
                    del wanted[name]
            await asyncio.sleep(0.1)

        if window.stacked_widget.currentWidget() is not window.results_page:
            recorder.stop()
            raise SystemExit("Оптимизация не дошла до отчёта — см. logs/winspector.log.")
        for name in wanted:
            print(f"  ! этап для {name} прошёл слишком быстро и не снят")

        recorder.set_phase("result")
        await asyncio.sleep(2.0)
        save_shot(window, directory, "05-report.png")
        await asyncio.sleep(3.0)
        recorder.stop()

        warn_about_personal_data(window.report_view.plain_text())
        compose_gif(recorder.frames, gif_path(language))

    return scenario


def warn_about_personal_data(report_text: str) -> None:
    """Имя учётной записи в отчёте — повод проверить снимок перед публикацией."""
    user = os.environ.get("USERNAME", "")
    if user and user.lower() in report_text.lower():
        print(
            f"  ! В отчёте встречается имя учётной записи «{user}». Проверьте "
            "05-report.png и demo-*.gif перед публикацией (docs/media/README.md)."
        )


# --- Запуск ------------------------------------------------------------------------


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except (AttributeError, OSError):
        return False


def relaunch_as_admin(language: str, *, offscreen: bool = False) -> None:
    """Повторяет команду с правами администратора и ждёт её завершения."""
    script = Path(__file__).resolve()
    extra = " --offscreen" if offscreen else ""
    command = (
        f"Start-Process -FilePath '{sys.executable}' "
        f"-ArgumentList '\"{script}\" --run --language {language}{extra} --elevated' "
        f"-WorkingDirectory '{PROJECT_ROOT}' -Verb RunAs -Wait"
    )
    print("Нужны права администратора: подтвердите запрос Windows.")
    subprocess.run(["powershell", "-NoProfile", "-Command", command], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--static", action="store_true", help="снимки 01 и 02, без изменений системы")
    mode.add_argument(
        "--run", action="store_true", help="настоящая оптимизация: GIF и снимки 03–05"
    )
    parser.add_argument(
        "--language", choices=LANGUAGES, required=True, help="язык интерфейса на снимках"
    )
    parser.add_argument(
        "--offscreen", action="store_true", help="не показывать окно на экране во время съёмки"
    )
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    force_language(args.language)

    if args.static:
        print(f"Снимки главного экрана и окна подключения Gemini ({args.language})...")
        scenario = functools.partial(static_scenario, language=args.language)
        run_scenario(build_application(), scenario, offscreen=args.offscreen)
        return 0

    if not is_admin():
        relaunch_as_admin(args.language, offscreen=args.offscreen)
        return 0

    frames_dir = Path(tempfile.mkdtemp(prefix="winspector-frames-"))
    try:
        print(f"Запись настоящего запуска оптимизации ({args.language})...")
        run_scenario(
            build_application(),
            make_run_scenario(frames_dir, args.language),
            offscreen=args.offscreen,
        )
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)
        if args.elevated:
            input("Готово. Нажмите Enter, чтобы закрыть окно...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
