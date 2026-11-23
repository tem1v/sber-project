"""Детерминированный экстрактор на правилах — заглушка вместо LLM.

Нужен, чтобы backend, БД, тесты и демо работали без API-ключей.
Боевой вариант (GigaChat и т.п.) — app/ai/extraction.py, зона Участника 1.
"""

import re

from app.ai.schemas import Address, AnalysisResult, Topic, Urgency
from app.core.enums import UrgencyLevel
from app.core.taxonomy import ADDRESS_REQUIRED_TOPICS

MODEL_VERSION = "rule-based-0.1"

# (тема, подтема, ключевые фрагменты в нижнем регистре, «ё» → «е»)
TOPIC_RULES: list[tuple[str, str | None, tuple[str, ...]]] = [
    ("Аварийная ситуация", None, ("запах газа", "пахнет газом", "утечка газа", "пожар", "возгоран", "задымлен", "обрушен")),
    ("ЖКХ", "Отопление", ("отоплен", "батаре", "радиатор", "холодно в квартир")),
    ("ЖКХ", "Водоснабжение", ("воды", "вода ", "водой", "кран", "протечк", "протека", "труб", "канализац")),
    ("ЖКХ", "Электроснабжение", ("электричеств", "свет в подъезде", "без света", "искрит", "проводк", "розетк")),
    ("ЖКХ", "Лифт", ("лифт",)),
    ("Дороги", "Ямы", ("яма", "ямы", "ямой", "ямах", "асфальт", "выбоин")),
    ("Дороги", "Освещение", ("фонар", "уличное освещение", "освещени")),
    ("Благоустройство", "Мусор", ("мусор", "свалк", "контейнер", "отход")),
    ("Благоустройство", "Озеленение", ("дерев", "газон", "кустарник")),
    ("Транспорт", None, ("автобус", "остановк", "маршрут", "трамва", "троллейбус")),
    ("Социальная защита", None, ("пособи", "пенси", "льгот", "инвалид", "многодетн", "субсиди")),
    ("Образование", None, ("школ", "детский сад", "детского сада", "детсад", "садик")),
]

CRITICAL_SIGNALS = (
    "запах газа", "пахнет газом", "утечка газа", "пожар", "возгоран", "задымлен",
    "обрушен", "застрял в лифте", "застряли в лифте", "угроза жизни", "угрожает жизни",
    "оголенн", "искрит",
)
HIGH_SIGNALS = (
    "прорыв", "затопил", "затоплен", "кипяток", "нет отопления", "без отопления",
    "без света", "упало дерево", "упавшее дерево", "аварийн",
)

_STREET = (
    r"(?i:улиц[аеуы]|ул\.?|проспект[а-я]*|пр-т|переул[а-я]*|пер\.|шоссе|бульвар[а-я]*|б-р"
    r"|площад[ьи]|пл\.|набережн[а-я]*|наб\.|проезд[а-я]*|микрорайон[а-я]*|мкр\.?)"
)
_HOUSE = r"(?i:дом[аеу]?|д\.)\s*№?\s*(\d+[а-яА-Я]?(?:,?\s*(?:корпус|корп\.?|к\.?|стр\.?)\s*\d+)?)"
STREET_RE = re.compile(
    rf"{_STREET}\s+[А-ЯЁA-Z0-9][\w\-]*(?:\s+[А-ЯЁ][\w\-]*)?(?:,?\s*(?:{_HOUSE}|(\d+[а-яА-Я]?)\b))?"
)
HOUSE_RE = re.compile(_HOUSE)
SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def extract_address(text: str) -> str | None:
    """Адрес/ориентир дословно из текста (улица [+ дом]) или None."""
    m = STREET_RE.search(text)
    if not m:
        return None
    address = m.group(0).strip(" ,")
    if not re.search(r"\d", address):
        house = HOUSE_RE.search(text)
        if house:
            address = f"{address}, д. {house.group(1)}"
    return address


def detect_topics(text: str) -> list[Topic]:
    low = _norm(text)
    found: list[Topic] = []
    for name, sub, keys in TOPIC_RULES:
        hits = sum(1 for k in keys if k in low)
        if hits:
            found.append(Topic(name=name, subtopic=sub, confidence=round(min(0.95, 0.6 + 0.1 * (hits - 1)), 2)))
    found.sort(key=lambda t: t.confidence, reverse=True)
    return found


def detect_urgency(text: str) -> Urgency:
    low = _norm(text)
    critical = [s for s in CRITICAL_SIGNALS if s in low]
    high = [s for s in HIGH_SIGNALS if s in low]
    if critical:
        level = UrgencyLevel.CRITICAL
    elif high:
        level = UrgencyLevel.HIGH
    else:
        level = UrgencyLevel.NORMAL
    signals = critical + high
    return Urgency(level=level, risk_signals=signals, requires_human_review=bool(signals))


def _evidence(text: str, keys: list[str]) -> list[str]:
    out: list[str] = []
    for sentence in SENTENCE_RE.findall(text):
        s = sentence.strip()
        if s and any(k in _norm(s) for k in keys) and s not in out:
            out.append(s)
        if len(out) == 3:
            break
    return out


def _summary(text: str) -> str:
    first = next((s.strip() for s in SENTENCE_RE.findall(text) if s.strip()), text.strip())
    return first if len(first) <= 160 else first[:157].rstrip() + "…"


def extract(text: str) -> AnalysisResult:
    text = text.strip()
    topics = detect_topics(text)
    urgency = detect_urgency(text)
    address_raw = extract_address(text)

    missing: list[str] = []
    questions: list[str] = []
    if not topics:
        missing.append("суть проблемы")
        questions.append("Опишите, пожалуйста, подробнее, в чём заключается проблема и где она возникла.")
    needs_address = any(t.name in ADDRESS_REQUIRED_TOPICS for t in topics)
    if needs_address and not address_raw:
        missing.append("адрес")
        questions.append("Уточните, пожалуйста, точный адрес (улица, дом) или ориентир, где наблюдается проблема.")

    if not topics:
        confidence = 0.05 if len(text) < 20 else 0.2
    else:
        confidence = topics[0].confidence
        if needs_address and not address_raw:
            confidence *= 0.75
        if len({t.name for t in topics}) > 1:
            confidence *= 0.9

    if confidence < 0.5 or len({t.name for t in topics}) > 1:
        urgency.requires_human_review = True

    matched_keys = [k for _, _, keys in TOPIC_RULES for k in keys] + list(CRITICAL_SIGNALS + HIGH_SIGNALS)
    low = _norm(text)
    evidence = _evidence(text, [k for k in matched_keys if k in low])

    return AnalysisResult(
        summary=_summary(text) if text else None,
        address=Address(raw=address_raw, normalized=None, confidence=0.8 if address_raw else 0.0),
        topics=topics,
        urgency=urgency,
        missing_information=missing,
        clarifying_questions=questions,
        evidence=evidence,
        overall_confidence=round(confidence, 2),
        model_version=MODEL_VERSION,
        prompt_version=None,
    )
