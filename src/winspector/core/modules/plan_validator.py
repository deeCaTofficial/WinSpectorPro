# src/winspector/core/modules/plan_validator.py
"""
Слой безопасности: валидация плана оптимизации, полученного от ИИ.

Ключевой принцип — ИИ не является доверенным источником. Его ответ проходит
через несколько независимых барьеров, и ни один из них не полагается на
данные, сгенерированные самим ИИ:

1.  Синтаксис: идентификатор обязан соответствовать строгому шаблону.
    Это исключает инъекцию в PowerShell-команду на уровне данных.
2.  Жёсткий денилист: список критических компонентов Windows зашит в код.
    Он не редактируется базой знаний и не зависит от ответа модели.
3.  База знаний: правила с `safety: critical` и явной защитой по профилю
    (`protected_for_profiles`).
4.  План очистки: разрешены только категории, которые нашёл наш собственный
    сканер; пути к файлам от ИИ игнорируются полностью.

Семантика полей базы знаний:
    relevant_profiles     — для каких профилей правило вообще уместно
                            (используется для отбора правил в промпт);
    protected_for_profiles — для каких профилей компонент трогать НЕЛЬЗЯ;
    safety                — critical | low | medium | high.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

# Идентификатор службы/пакета. Кавычки, пробелы, точки с запятой и любые
# другие метасимволы PowerShell исключены на уровне шаблона.
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-+]{1,256}$")

ALLOWED_ACTIONS = {"disable", "set_manual", "stop", "remove"}
ALLOWED_TYPES = {"service", "uwp_app", "startup_item"}

# Службы, отключение которых ломает загрузку, безопасность или сам механизм
# восстановления системы. Списком управляет только код.
CRITICAL_SERVICES: frozenset[str] = frozenset(
    {
        # Ядро RPC/COM — без них система не загружается.
        "dcomlaunch",
        "rpcss",
        "rpceptmapper",
        "brokerinfrastructure",
        "coremessagingregistrar",
        "systemeventsbroker",
        "timebrokersvc",
        # Сессии, профили, учётные записи.
        "lsm",
        "profsvc",
        "usermanager",
        "samss",
        "gpsvc",
        # Оборудование и питание.
        "plugplay",
        "power",
        "deviceinstall",
        "shellhwdetection",
        # Журналы, планировщик, WMI — от них зависит диагностика и сам продукт.
        "eventlog",
        "eventsystem",
        "sens",
        "schedule",
        "winmgmt",
        "dps",
        # Сеть.
        "nsi",
        "dhcp",
        "dnscache",
        "nlasvc",
        "netprofm",
        # Безопасность и криптография.
        "bfe",
        "mpssvc",
        "cryptsvc",
        "windefend",
        "wscsvc",
        "sense",
        "securityhealthservice",
        "keyiso",
        "vaultsvc",
        # Обновления и установка компонентов.
        "wuauserv",
        "msiserver",
        "trustedinstaller",
        "bits",
        # Теневое копирование — на нём держатся точки восстановления,
        # которые создаёт само приложение.
        "vss",
        "swprv",
        # Интерфейс и хранилище состояния оболочки.
        "themes",
        "staterepository",
        "tiledatamodelsvc",
        # Аудио: отключение делает систему «немой».
        "audiosrv",
        "audioendpointbuilder",
    }
)

# UWP-пакеты, удаление которых ломает оболочку, «Параметры» или установку
# приложений. Сравнение идёт по префиксу, т.к. в имени есть версия и хеш.
CRITICAL_UWP_PREFIXES: tuple[str, ...] = (
    "microsoft.windows.shellexperiencehost",
    "microsoft.windows.startmenuexperiencehost",
    "microsoft.windows.cloudexperiencehost",
    "microsoft.windows.immersivecontrolpanel",
    "windows.immersivecontrolpanel",
    "microsoft.windows.search",
    "microsoft.windows.searchui",
    "microsoft.aad.brokerplugin",
    "microsoft.accountscontrol",
    "microsoft.lockapp",
    "microsoft.credentialdialoghost",
    "microsoft.windows.apprep.chxapp",
    "microsoft.sechealthui",
    "microsoft.windows.sechealthui",
    "microsoft.desktopappinstaller",
    "microsoft.windowsstore",
    "microsoft.storepurchaseapp",
    "microsoft.vclibs",
    "microsoft.net.native",
    "microsoft.ui.xaml",
    "microsoft.services.store.engagement",
    "microsoft.xboxgamecallableui",
    "microsoft.windows.xgpuejectdialog",
    "microsoft.windows.pinningconfirmationdialog",
)

# Профили, для которых кеши сборки и пакетных менеджеров — рабочие данные,
# а не мусор.
SENSITIVE_PROFILES: frozenset[str] = frozenset(
    {"developer", "content_creator", "audio_engineer", "designer"}
)

CANONICAL_PROFILES: frozenset[str] = frozenset(
    {
        "gamer",
        "developer",
        "designer",
        "office_worker",
        "streamer",
        "content_creator",
        "audio_engineer",
        "power_user",
        "home_user",
    }
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_profile(value: str) -> str:
    """
    Приводит имя профиля к каноническому виду.

    База знаний использует `power_user`, а модель отвечает `PowerUser` —
    без нормализации эти значения никогда не совпадут.

    >>> normalize_profile("PowerUser"), normalize_profile("power_user")
    ('power_user', 'power_user')
    """
    text = _CAMEL_BOUNDARY.sub("_", str(value).strip())
    return _NON_ALNUM.sub("_", text.lower()).strip("_")


def normalize_profiles(values: Iterable[str] | None) -> set[str]:
    """Нормализует набор профилей, отбрасывая пустые значения."""
    if not values:
        return set()
    return {p for p in (normalize_profile(v) for v in values) if p}


def is_safe_identifier(value: Any) -> bool:
    """Проверяет, что идентификатор безопасно подставлять в команду."""
    return isinstance(value, str) and bool(SAFE_ID_PATTERN.match(value))


def is_critical_service(service_id: str) -> bool:
    """Проверяет принадлежность службы к жёсткому денилисту."""
    return service_id.strip().lower() in CRITICAL_SERVICES


def is_critical_uwp(package_id: str) -> bool:
    """Проверяет принадлежность UWP-пакета к жёсткому денилисту."""
    normalized = package_id.strip().lower()
    return any(normalized.startswith(prefix) for prefix in CRITICAL_UWP_PREFIXES)


def rule_targets(rule: dict[str, Any]) -> set[str]:
    """
    Возвращает реальные идентификаторы, которых касается правило.

    Правило может задавать `targets` явно; если поля нет, используется `id`
    без служебного префикса (`Svc_MapsBroker` -> `mapsbroker`).
    """
    targets = rule.get("targets")
    if isinstance(targets, list) and targets:
        return {str(t).strip().lower() for t in targets if str(t).strip()}

    rule_id = str(rule.get("id", "")).strip()
    if not rule_id:
        return set()
    return {re.sub(r"^(svc|app|startup)_", "", rule_id.lower())}


class PlanValidator:
    """
    Приводит план от ИИ к безопасному для исполнения виду.

    Небезопасные пункты отбрасываются с записью в лог, а не приводят к
    отказу от всего плана: одно спорное предложение модели не должно
    отменять десяток полезных.
    """

    def __init__(
        self,
        knowledge_base: dict[str, Any],
        user_profiles: Iterable[str],
        known_junk_categories: Iterable[str] | None = None,
    ) -> None:
        self.user_profiles = normalize_profiles(user_profiles)

        optimization_rules = knowledge_base.get("optimization_rules") or []
        cleanup_rules = knowledge_base.get("cleanup_rules") or []

        self.cleanup_rules: dict[str, dict[str, Any]] = {
            str(rule["category_id"]): rule
            for rule in cleanup_rules
            if isinstance(rule, dict) and rule.get("category_id")
        }

        # Идентификаторы, запрещённые базой знаний.
        self._kb_critical: set[str] = set()
        self._kb_protected: set[str] = set()
        for rule in optimization_rules:
            if not isinstance(rule, dict):
                continue
            targets = rule_targets(rule)
            if not targets:
                continue
            if str(rule.get("safety", "")).lower() == "critical":
                self._kb_critical |= targets
            protected = normalize_profiles(rule.get("protected_for_profiles"))
            if protected & self.user_profiles:
                self._kb_protected |= targets

        # Категории мусора, реально найденные нашим сканером.
        self.known_junk_categories: set[str] | None = (
            set(known_junk_categories) if known_junk_categories is not None else None
        )

        self.rejected: list[str] = []

    # --- Публичный API ----------------------------------------------------

    def validate(self, plan: Any) -> dict[str, Any]:
        """Возвращает очищенный план с ключами `action_plan` и `cleanup_plan`."""
        if not isinstance(plan, dict):
            raise ValueError(f"План должен быть словарём, получен {type(plan).__name__}.")

        action_plan = self._validate_action_plan(plan.get("action_plan") or [])
        cleanup_plan = self._validate_cleanup_plan(plan.get("cleanup_plan") or {})

        logger.info(
            "Валидация плана завершена: одобрено %d действий, %d категорий очистки, "
            "отклонено %d пунктов.",
            len(action_plan),
            sum(1 for d in cleanup_plan.values() if d.get("clean")),
            len(self.rejected),
        )
        return {"action_plan": action_plan, "cleanup_plan": cleanup_plan}

    # --- Действия над компонентами ---------------------------------------

    def _reject(self, reason: str) -> None:
        self.rejected.append(reason)
        logger.warning("ОТКЛОНЕНО: %s", reason)

    def _validate_action_plan(self, action_plan: Any) -> list[dict[str, Any]]:
        if not isinstance(action_plan, list):
            raise ValueError(
                f"'action_plan' должен быть списком, получен {type(action_plan).__name__}."
            )

        safe_actions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        for action in action_plan:
            checked = self._validate_action(action)
            if checked is None:
                continue
            key = (checked["type"], checked["id"].lower())
            if key in seen:
                self._reject(f"дубликат действия для '{checked['id']}'")
                continue
            seen.add(key)
            safe_actions.append(checked)

        return safe_actions

    def _validate_action(self, action: Any) -> dict[str, Any] | None:
        if not isinstance(action, dict):
            self._reject(f"пункт плана не является объектом: {action!r}")
            return None

        item_id = action.get("id")
        item_type = action.get("type")
        item_action = action.get("action")

        if not is_safe_identifier(item_id):
            self._reject(f"недопустимый идентификатор: {item_id!r}")
            return None
        if item_type not in ALLOWED_TYPES:
            self._reject(f"неизвестный тип '{item_type}' для '{item_id}'")
            return None
        if item_action not in ALLOWED_ACTIONS:
            self._reject(f"неизвестное действие '{item_action}' для '{item_id}'")
            return None

        package_full_name = action.get("package_full_name")
        if package_full_name is not None and not is_safe_identifier(package_full_name):
            self._reject(f"недопустимый package_full_name для '{item_id}'")
            return None

        normalized_id = item_id.lower()

        # Барьер 2: жёсткий денилист в коде.
        if item_type == "service" and is_critical_service(normalized_id):
            self._reject(f"критическая служба Windows '{item_id}'")
            return None
        if item_type == "uwp_app" and (
            is_critical_uwp(normalized_id)
            or (package_full_name and is_critical_uwp(package_full_name))
        ):
            self._reject(f"критический системный пакет '{item_id}'")
            return None

        # Барьер 3: запреты из базы знаний.
        if normalized_id in self._kb_critical:
            self._reject(f"компонент '{item_id}' помечен в базе знаний как critical")
            return None
        if item_action in {"disable", "remove"} and normalized_id in self._kb_protected:
            self._reject(
                f"действие '{item_action}' над '{item_id}': компонент защищён "
                f"для профилей {sorted(self.user_profiles)}"
            )
            return None

        result: dict[str, Any] = {
            "type": item_type,
            "id": item_id,
            "action": item_action,
            "reason": str(action.get("reason", "")),
            "user_explanation_ru": str(action.get("user_explanation_ru", "")),
        }
        if package_full_name:
            result["package_full_name"] = package_full_name
        return result

    # --- План очистки -----------------------------------------------------

    def _validate_cleanup_plan(self, cleanup_plan: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(cleanup_plan, dict):
            raise ValueError(
                f"'cleanup_plan' должен быть словарём, получен {type(cleanup_plan).__name__}."
            )

        safe_plan: dict[str, dict[str, Any]] = {}
        is_sensitive = bool(self.user_profiles & SENSITIVE_PROFILES)

        for category_id, decision in cleanup_plan.items():
            if not isinstance(decision, dict) or "clean" not in decision:
                self._reject(f"некорректное решение об очистке для '{category_id}'")
                continue

            wants_clean = bool(decision.get("clean"))
            if not wants_clean:
                safe_plan[str(category_id)] = {"clean": False}
                continue

            rule = self.cleanup_rules.get(str(category_id))
            if rule is None:
                self._reject(f"очистка неизвестной категории '{category_id}'")
                continue

            # Категория должна присутствовать в результатах нашего сканера:
            # ИИ не может «придумать» цель для удаления.
            if (
                self.known_junk_categories is not None
                and category_id not in self.known_junk_categories
            ):
                self._reject(f"категория '{category_id}' отсутствует в результатах сканирования")
                continue

            safety = str(rule.get("safety", "medium")).lower()
            if safety == "critical":
                self._reject(f"очистка критической категории '{category_id}'")
                continue
            if safety == "low" and is_sensitive:
                self._reject(
                    f"очистка '{category_id}' (safety: low) для чувствительных "
                    f"профилей {sorted(self.user_profiles & SENSITIVE_PROFILES)}"
                )
                safe_plan[str(category_id)] = {"clean": False}
                continue
            if normalize_profiles(rule.get("protected_for_profiles")) & self.user_profiles:
                self._reject(
                    f"очистка '{category_id}' защищена для профилей {sorted(self.user_profiles)}"
                )
                safe_plan[str(category_id)] = {"clean": False}
                continue

            safe_plan[str(category_id)] = {"clean": True}

        return safe_plan
