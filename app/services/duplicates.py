"""Поиск похожих обращений. Только кандидаты — автоматического слияния нет."""

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.state import SimilarCandidate
from app.ai.rule_based import detect_topics, extract_address
from app.core.enums import RequestStatus
from app.core.taxonomy import ensure_aware
from app.db.models import Request

_STOP = {"пожалуйста", "просим", "прошу", "очень", "который", "которая", "уже", "этого", "этом", "нашем", "нашего"}
_ADDR_SKIP = re.compile(
    r"^(улиц\w*|ул|проспект\w*|пр|пр-т|переул\w*|пер|шоссе|ш|бульвар\w*|б-р|площад\w*|пл|набережн\w*|наб"
    r"|проезд\w*|микрорайон\w*|мкр|дом\w*|д|корп\w*|к|стр)$"
)


def _stems(text: str) -> set[str]:
    words = re.findall(r"[а-яa-z0-9]+", text.lower().replace("ё", "е"))
    return {w[:6] for w in words if len(w) > 3 and w not in _STOP}


def normalize_address(address: str | None) -> str | None:
    if not address:
        return None
    tokens = re.findall(r"[а-яa-z]+|\d+[а-я]?", address.lower().replace("ё", "е"))
    kept = [t for t in tokens if not _ADDR_SKIP.match(t)]
    return " ".join(kept) or None


def similarity(text_a: str, text_b: str, addr_a: str | None, addr_b: str | None, same_topic: bool) -> float:
    a, b = _stems(text_a), _stems(text_b)
    jaccard = len(a & b) / len(a | b) if a | b else 0.0
    na, nb = normalize_address(addr_a), normalize_address(addr_b)
    same_address = bool(na and nb and na == nb)
    return round(min(1.0, 0.4 * jaccard + 0.3 * same_address + 0.3 * same_topic), 3)


def find_similar(
    db: Session,
    *,
    request_id: int,
    text: str,
    address: str | None,
    topic: str | None,
    window_days: int,
    threshold: float,
    limit: int = 5,
) -> list[SimilarCandidate]:
    me = db.get(Request, request_id)
    since = ensure_aware(me.received_at) - timedelta(days=window_days) if me else None
    stmt = (
        select(Request)
        .where(Request.id != request_id, Request.status != RequestStatus.REJECTED.value)
        .order_by(Request.received_at.desc())
        .limit(500)
    )
    if since is not None:
        stmt = stmt.where(Request.received_at >= since)

    out: list[SimilarCandidate] = []
    for other in db.scalars(stmt):
        other_address = other.address or extract_address(other.raw_text)
        other_topic = other.topic or next((t.name for t in detect_topics(other.raw_text)), None)
        same_topic = bool(topic and other_topic and topic == other_topic)
        score = similarity(text, other.raw_text, address, other_address, same_topic)
        if score >= threshold:
            na, nb = normalize_address(address), normalize_address(other_address)
            out.append(
                SimilarCandidate(
                    request_id=other.id,
                    similarity=score,
                    summary=other.summary or other.raw_text[:160],
                    status=other.status,
                    same_address=bool(na and na == nb),
                    same_topic=same_topic,
                )
            )
    out.sort(key=lambda c: c.similarity, reverse=True)
    return out[:limit]
