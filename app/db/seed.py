"""Демо-данные: справочник ведомств, правила маршрутизации и вымышленные обращения.

Реальных персональных данных здесь нет и быть не должно.

    python -m app.db.seed                 # справочники + демо-обращения (если БД пустая)
    python -m app.db.seed --reset         # пересоздать схему (alembic downgrade/upgrade) и заполнить заново
    python -m app.db.seed --analyze       # дополнительно прогнать агента по новым обращениям
"""

import argparse
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.enums import Channel, RequestStatus
from app.core.taxonomy import utcnow
from app.db.models import Department, Request, RoutingRule

log = logging.getLogger(__name__)

DEPARTMENTS = [
    ("GKH", "Управление ЖКХ района", "Отопление, водо- и электроснабжение, лифты", "gkh@demo.local", "gkh"),
    ("ROADS", "Отдел дорожного хозяйства", "Состояние дорог и уличное освещение", "roads@demo.local", "roads"),
    ("BLAG", "Отдел благоустройства", "Вывоз мусора, озеленение, дворы", "blag@demo.local", "blag"),
    ("TRANSPORT", "Отдел транспорта", "Общественный транспорт и остановки", "transport@demo.local", "transport"),
    ("SOC", "Управление социальной защиты", "Пособия, льготы, поддержка", "soc@demo.local", "soc"),
    ("EDU", "Отдел образования", "Школы и детские сады", "edu@demo.local", "edu"),
    ("EDDS", "Единая дежурно-диспетчерская служба", "Аварии и угрозы жизни/здоровью", "edds@demo.local", "edds"),
    ("ARCHIVE", "Архивный отдел (упразднён)", "Неактивное ведомство для проверок", None, None),
]

# (тема, подтема, код ведомства, срок в днях, приоритет правила)
RULES = [
    ("Аварийная ситуация", None, "EDDS", 1, 100),
    ("ЖКХ", "Отопление", "GKH", 7, 10),
    ("ЖКХ", "Водоснабжение", "GKH", 7, 10),
    ("ЖКХ", "Электроснабжение", "GKH", 7, 10),
    ("ЖКХ", "Лифт", "GKH", 10, 10),
    ("ЖКХ", None, "GKH", 15, 0),
    ("Дороги", "Ямы", "ROADS", 30, 10),
    ("Дороги", "Освещение", "ROADS", 15, 10),
    ("Дороги", None, "ROADS", 30, 0),
    ("Благоустройство", "Мусор", "BLAG", 10, 10),
    ("Благоустройство", "Озеленение", "BLAG", 30, 10),
    ("Благоустройство", None, "BLAG", 30, 0),
    ("Транспорт", None, "TRANSPORT", 30, 0),
    ("Социальная защита", None, "SOC", 30, 0),
    ("Образование", None, "EDU", 30, 0),
]

DEMO_REQUESTS = [
    ("web", "Во дворе дома 5 по улице Ленина огромная яма на дороге, машины ломают подвеску. Просим отремонтировать.", 6),
    ("email", "На улице Ленина, д. 5 яма в асфальте прямо у въезда во двор, невозможно проехать.", 2),
    ("phone", "В подъезде по адресу ул. Садовая, д. 12 сильно пахнет газом! Соседи не открывают дверь.", 0),
    ("web", "Третий день нет отопления в квартире, батареи холодные. Проспект Мира, дом 31.", 1),
    ("social", "Возле школы опять не вывозят мусор, контейнеры переполнены.", 3),
    ("web", "Не работает лифт по адресу ул. Гагарина, д. 7, корп. 2, пожилые жители не могут спуститься.", 4),
    ("email", "Прошу разъяснить порядок получения пособия для многодетной семьи.", 5),
    ("visit", "Помогите", 1),
    ("web", "На остановке у дома 14 по улице Победы сломан навес, автобус номер 12 ходит раз в час.", 7),
    ("phone", "На ул. Строителей, д. 3 прорыв трубы, вода заливает подвал.", 0),
    ("web", "На улице Ленина у дома 9 не горит фонарь уже неделю, вечером темно и опасно.", 8),
    ("email", "Во дворе дома 20 на бульваре Космонавтов упало дерево и перегородило проезд, а ещё не вывозят мусор.", 1),
]


def seed_reference_data(db: Session) -> None:
    if db.scalar(select(func.count(Department.id))):
        return
    by_code: dict[str, Department] = {}
    for code, name, descr, contact, queue in DEPARTMENTS:
        dept = Department(code=code, name=name, description=descr, contact=contact, queue=queue, active=code != "ARCHIVE")
        db.add(dept)
        by_code[code] = dept
    db.flush()
    for topic, sub, code, days, prio in RULES:
        db.add(
            RoutingRule(
                topic=topic, subtopic=sub, department_id=by_code[code].id,
                deadline_days=days, priority=prio, conditions={}, active=True,
            )
        )
    db.flush()


def seed_demo_requests(db: Session) -> list[int]:
    if db.scalar(select(func.count(Request.id))):
        return []
    now = utcnow()
    ids = []
    for channel, text, days_ago in DEMO_REQUESTS:
        req = Request(
            source_channel=Channel(channel).value,
            raw_text=text,
            received_at=now - timedelta(days=days_ago, hours=len(ids)),
            status=RequestStatus.NEW.value,
            metadata_json={"demo": True},
        )
        db.add(req)
        db.flush()
        ids.append(req.id)
    return ids


def reset_schema(database_url: str | None = None) -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", str(root / "app/db/migrations"))
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-данные CivicFlow")
    parser.add_argument("--reset", action="store_true", help="пересоздать схему и данные")
    parser.add_argument("--analyze", action="store_true", help="прогнать агента по новым демо-обращениям")
    parser.add_argument("--no-requests", action="store_true", help="только справочники")
    args = parser.parse_args()
    logging.basicConfig(level="INFO")

    from app.core.config import get_settings
    from app.db.session import get_session_factory

    if args.reset:
        reset_schema()
    with get_session_factory()() as db:
        seed_reference_data(db)
        ids = [] if args.no_requests else seed_demo_requests(db)
        db.commit()
        log.info("Справочники готовы, демо-обращений добавлено: %d", len(ids))
        if args.analyze and ids:
            from app.agent.base import build_agent
            from app.core.errors import ApiError
            from app.services.analysis import run_analysis

            settings = get_settings()
            agent = build_agent(settings.agent_mode)
            for rid in ids:
                try:
                    res = run_analysis(db, rid, agent, settings)
                    log.info("#%d → %s", rid, res.status)
                except ApiError as exc:
                    log.warning("#%d → ошибка анализа: %s", rid, exc.message)


if __name__ == "__main__":
    main()
