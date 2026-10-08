# Media for README

**English** | [Русский](#русский)

The README shows a demo GIF and four screenshots (01, 02, 03, 05): English ones in `README.md`, Russian ones in
`README_RU.md`. Only real captures of the current version belong here — no mock-ups.

## Files

| File | What it must show |
| --- | --- |
| `demo-en.gif`, `demo-ru.gif` | Full scenario: launch → PC analysis → recommendations → optimization → result |
| `screenshots/<lang>/01-main-screen.png` | Main screen: **Optimize** button and the mode line (*Without AI · safe mode* or *Gemini connected*) |
| `screenshots/<lang>/02-gemini-key.png` | Gemini API key window, as opened from Settings → Gemini, with the key field empty |
| `screenshots/<lang>/03-analysis.png` | Progress screen during analysis: *Analyzing the system…* / «Анализ вашего стиля работы...» |
| `screenshots/<lang>/04-optimization.png` | Progress screen: *Optimizing…* / «Применение оптимизаций и очистка системы...». Optional: on a fast run this stage may pass before it is captured, so the README does not show it |
| `screenshots/<lang>/05-report.png` | **Report page**: totals, programs waiting to be closed, system changes |

`<lang>` is `en` or `ru`. All files are captured from version 1.1.0; re-capture them when the
interface changes.

The app has no separate plan screen: the plan is built and validated automatically, and the
actions that were applied are listed in the final report. Show the plan through the report and
the analysis stage, not through an edited image.

## Capturing

`scripts/capture_media.py` builds the real app window and saves frames of the live widgets
(`QWidget.grab()`); nothing is drawn or edited. `--language` sets the interface language for the
capture only; the language chosen in the app's settings is not read or changed.

```powershell
.venv\Scripts\pip install -r requirements-dev.txt   # includes Pillow for the GIF

# 01 and 02: no administrator rights, nothing on the PC is changed
.venv\Scripts\python scripts\capture_media.py --static --language en
.venv\Scripts\python scripts\capture_media.py --static --language ru

# GIF and 03–05: a REAL optimization of this PC
.venv\Scripts\python scripts\capture_media.py --run --language en
```

Add `--offscreen` to render the window in memory without showing it: the frames are the same,
and the capture does not get in the way of whatever else is on the screen. The administrator
prompt still appears.

**`--run` really optimizes the computer it runs on:** it asks for administrator rights, creates
a restore point, changes services, cleans junk and moves program leftovers to quarantine.
Run it on a test virtual machine, or only on a PC where you would run WinSpector Pro anyway.
Windows allows one restore point per 24 hours, so a second `--run` on the same day (for the other
language) reuses the earlier point.

Whether the run uses Gemini depends on the key saved in the app. The GIF keeps the start and the
result at real speed and speeds up the long processing stage to about 30 seconds; the script
prints the final length, size and speed-up factor.

## Before publishing

- No real API key on screen.
- No personal data: the report may contain paths with your Windows user name (for example, the
  quarantine folder). The script warns if the report contains the account name; crop or blur it,
  or use a VM with a neutral user name.
- Numbers in images (freed space, file counts) must come from the recorded run. Do not edit them.

## Inserting into the README

Replace the placeholders in `README.md` (English files) and `README_RU.md` (Russian files):

```html
<p align="center"><img src="./docs/media/demo-en.gif" alt="WinSpector Pro demo" width="800"></p>
```

```html
<img src="./docs/media/screenshots/en/03-analysis.png" alt="System analysis" width="400">
```

---

## Русский

В README показаны демо-GIF и четыре скриншота (01, 02, 03, 05): английские — в `README.md`, русские — в
`README_RU.md`. Сюда кладутся только настоящие снимки текущей версии — никаких макетов.

**Файлы** — те же, что в таблице выше: `demo-ru.gif` / `demo-en.gif` (полный сценарий: запуск →
анализ ПК → рекомендации → оптимизация → результат) и `screenshots/<язык>/01…05`:

1. главный экран: кнопка «Оптимизировать» и строка режима («Без ИИ · безопасный режим» или
   «Gemini подключён»);
2. окно ключа Gemini, открытое из «Настройки → Gemini», с пустым полем ключа;
3. анализ: «Анализ вашего стиля работы...»;
4. выполнение: «Применение оптимизаций и очистка системы...» — по желанию: на быстром прогоне этап может пройти раньше, чем его снимут, поэтому в README его нет;
5. страница отчёта: итог, программы, которые ждут закрытия, изменения в системе.

Все файлы сняты с версии 1.1.0; переснимайте их, когда меняется интерфейс.

Отдельного экрана с планом в программе нет: план составляется и проверяется автоматически, а
применённые действия перечислены в отчёте. Показывайте план через отчёт и этап анализа, а не
через отредактированную картинку.

**Съёмка.** `scripts/capture_media.py` строит настоящее окно программы и снимает кадры с живых
виджетов — ничего не рисуется и не редактируется. `--language ru|en` задаёт язык интерфейса только
на время съёмки; язык, выбранный в настройках программы, не читается и не меняется. Команды — в
разделе «Capturing» выше. С `--offscreen` окно рисуется в памяти и не появляется на экране —
кадры те же, а съёмка не мешает работать; запрос прав администратора при этом всё равно будет.

**`--run` действительно оптимизирует компьютер, на котором запущен:** просит права
администратора, создаёт точку восстановления, меняет службы, чистит мусор и переносит остатки
программ в карантин. Запускайте на тестовой виртуальной машине или только там, где и так
собирались запустить WinSpector Pro. Windows создаёт не больше одной точки восстановления в сутки,
поэтому второй `--run` в тот же день (для другого языка) использует уже созданную точку.

С ИИ или без него пройдёт запуск — зависит от ключа, сохранённого в программе. Начало и итог в GIF
идут в реальном времени, долгая обработка ускоряется примерно до 30 секунд; длину, размер и
коэффициент ускорения скрипт выводит в конце.

**Перед публикацией:** на экране нет настоящего ключа; пути с именем учётной записи (например,
папка карантина в отчёте) обрезаны или размыты — скрипт предупредит, если имя есть в отчёте;
цифры на изображениях — из записанного запуска и не отредактированы.

**Вставка:** замените заглушки в `README.md` (английские файлы) и `README_RU.md` (русские) разметкой
из раздела выше.
