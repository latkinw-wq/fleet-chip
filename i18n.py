"""i18n — строки приборки на двух языках и выбор языка из настроек плагина.

Язык задаётся настройкой ``language``: ``auto`` (по локали системы), ``ru`` или ``en``.
Незнакомая локаль и мусор в настройке дают английский: репозиторий публичный, чужого
человека он не должен встречать русским текстом.

Модуль грузится по пути к файлу (см. ``fleet_data._i18n``): gateway импортирует
``fleet_data.py`` как bare-модуль, каталога плагина в ``sys.path`` нет, а ``import i18n``
подхватил бы одноимённый модуль из чужого пакета.
"""

from __future__ import annotations

import os

SETTING_CHOICES = ("auto", "ru", "en")
DEFAULT_SETTING = "auto"
# Куда уходим, когда язык не определён: английский, а не русский.
FALLBACK = "en"

# Локали, для которых русский — родной выбор по умолчанию.
_RU_LOCALES = ("ru", "be", "uk", "kk", "ky", "tg", "hy", "az")
# Windows называет локаль словами («Russian_Russia»), а не кодом («ru_RU»).
_LANGUAGE_NAMES = {"russian": "ru", "ukrainian": "uk", "belarusian": "be", "kazakh": "kk",
                   "kyrgyz": "ky", "tajik": "tg", "armenian": "hy", "azeri": "az"}
# LANG=C/POSIX означают «предпочтений нет», а не английский: тогда решает ОС.
_NO_PREFERENCE = ("c", "posix")

_STRINGS = {
    "ru": {
        # --- running ---
        "lost_reaped": "воркер снят диспетчером, карточка осталась",
        "lost_dead": "воркер умер: heartbeat %.0f мин назад",
        "silent": "тишина: heartbeat %.0f мин назад",
        "never_model": "воркер не доехал до модели: 0 вызовов за %.0f мин",
        "working_calls": "работает: %s вызовов модели, heartbeat %.0f мин назад",
        "working_start": "старт: heartbeat %.0f мин назад, модель ещё не вызывалась",
        "api_unknown": "не знаю: сессия воркера не найдена, вызовы модели неизвестны"
                       " (heartbeat %.0f мин назад)",
        "working_no_session": "старт без сессии в state.db (heartbeat %.0f мин назад)",
        # --- blocked ---
        "blocked_ask_reason": "просит решения: %s",
        "blocked_ask_kind": "просит решения: блок %s",
        "kind_missing": "без объяснения",
        "blocked_wait_no_parents": "ждёт зависимостей: связи родителей не прочитать",
        "blocked_wait_parents": "ждёт родителей: не завершено %d",
        "blocked_stale_parents": "родители закрыты, авто-продвижение не сработало",
        "blocked_initial": "отложена: создана сразу блокированной",
        "blocked_bare": "блок без объяснения — проверь карточку",
        # --- parked ---
        "parked_stale": "отложена %.0f дн (%s) — пора решить",
        "parked": "отложена штатно (%s), %.0f дн",
        "why_wait_marker": "маркер [wait]",
        "why_scheduled": "status scheduled",
        # --- not started ---
        "not_started": "не стартовала: %.0f мин без движения",
        # --- /fleet text ---
        "render_header": (
            "🛰 FLEET · работают {working} · тишина {running_silent} · без модели {never_model}"
            " · не стартовали {not_started} · просят решения {blocked_ask}"
            " · висят зря {blocked_stale} · просрочено отложенных {parked_stale}"
            " · потеряны {lost} · тихо ждут {blocked_wait} · на паузе {parked}"
            " · закрыто за 6ч {done_6h}"
        ),
        "render_row": "%s %s · %s · %s",
        "render_empty": "   пусто: карточек, требующих внимания, нет",
        "render_cron": "   кроны: %s",
        "render_failing": " · сбоят: %s",
        "render_delivery": " · недоставок за 24ч: %s",
        "render_error": "   ⚠ %s — %s",
        "no_assignee": "—",
    },
    "en": {
        # --- running ---
        "lost_reaped": "worker reaped by the dispatcher, the card stayed behind",
        "lost_dead": "worker died: heartbeat %.0f min ago",
        "silent": "silent: heartbeat %.0f min ago",
        "never_model": "worker never reached the model: 0 calls in %.0f min",
        "working_calls": "working: %s model calls, heartbeat %.0f min ago",
        "working_start": "starting: heartbeat %.0f min ago, model not called yet",
        "api_unknown": "not sure: worker session not found, model calls unknown"
                       " (heartbeat %.0f min ago)",
        "working_no_session": "starting without a session in state.db (heartbeat %.0f min ago)",
        # --- blocked ---
        "blocked_ask_reason": "needs a decision: %s",
        "blocked_ask_kind": "needs a decision: %s block",
        "kind_missing": "unexplained",
        "blocked_wait_no_parents": "waiting on dependencies: parent links unreadable",
        "blocked_wait_parents": "waiting on parents: %d still open",
        "blocked_stale_parents": "parents are closed, auto-promotion never fired",
        "blocked_initial": "parked: blocked from the start",
        "blocked_bare": "blocked without a reason — check the card",
        # --- parked ---
        "parked_stale": "parked %.0f d (%s) — time to decide",
        "parked": "parked on purpose (%s), %.0f d",
        "why_wait_marker": "the [wait] marker",
        "why_scheduled": "native scheduled status",
        # --- not started ---
        "not_started": "never started: %.0f min without movement",
        # --- /fleet text ---
        "render_header": (
            "🛰 FLEET · working {working} · silent {running_silent} · no model {never_model}"
            " · not started {not_started} · need a decision {blocked_ask}"
            " · stale blocks {blocked_stale} · overdue parked {parked_stale}"
            " · lost {lost} · waiting {blocked_wait} · on hold {parked}"
            " · done in 6h {done_6h}"
        ),
        "render_row": "%s %s · %s · %s",
        "render_empty": "   empty: no card needs attention",
        "render_cron": "   cron: %s",
        "render_failing": " · failing: %s",
        "render_delivery": " · undelivered in 24h: %s",
        "render_error": "   ⚠ %s — %s",
        "no_assignee": "—",
    },
}

# Windows LANGID → язык, когда переменных локали нет (обычный запуск gateway).
_WIN_LANGID = {0x19: "ru", 0x22: "uk", 0x23: "be", 0x3F: "kk", 0x2C: "az", 0x2B: "hy"}


def resolve(setting=None) -> str:
    """Язык снимка: явная настройка ``ru``/``en`` важнее локали, всё прочее — ``auto``."""
    want = str(setting or "").strip().lower()
    if want in ("ru", "en"):
        return want
    return detect()


def detect() -> str:
    """``ru`` или ``en`` по локали системы; незнакомое — ``en``."""
    return "ru" if _language_of(system_locale()) in _RU_LOCALES else FALLBACK


def _language_of(value: str) -> str:
    """``ru_RU.UTF-8`` / ``Russian_Russia`` / ``ru`` → код языка."""
    primary = (value or "").strip().lower().split(".")[0].split("@")[0]
    primary = primary.replace("-", "_").split("_")[0]
    if not primary:
        return ""
    return _LANGUAGE_NAMES.get(primary, primary[:2])


def _is_no_preference(value: str) -> bool:
    """``C``/``POSIX``/``C.UTF-8`` — «локаль не выбрана», а не «английский»."""
    return (value or "").strip().lower().split(".")[0].split("@")[0].split("_")[0] in _NO_PREFERENCE


def _windows_ui_language() -> str:
    """Язык интерфейса Windows: для плагина он точнее локали форматов (``locale``)."""
    if os.name != "nt":
        return ""
    try:
        import ctypes

        return _WIN_LANGID.get(ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF, "")
    except Exception:  # noqa: BLE001 — нет ctypes/windll: остаются другие источники
        return ""


def system_locale() -> str:
    """Локаль системы: env → язык интерфейса ОС → ``locale``; пусто — не определили."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = (os.environ.get(var) or "").strip()
        if value and not _is_no_preference(value):
            return value
    ui = _windows_ui_language()
    if ui:
        return ui
    try:
        import locale as _locale

        name = _locale.getlocale(_locale.LC_CTYPE)[0] or ""
        if name:
            return name
    except Exception:  # noqa: BLE001 — локаль не критична, ниже остаётся FALLBACK
        pass
    return ""


def t(lang, key, *args) -> str:
    """Строка ``key`` на языке ``lang``; неизвестный язык и ключ уходят в английский."""
    table = _STRINGS.get(str(lang or "").lower()) or _STRINGS[FALLBACK]
    template = table.get(key) or _STRINGS[FALLBACK].get(key) or key
    return template % args if args else template
