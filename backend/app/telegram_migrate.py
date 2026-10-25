"""Run explicitly BEFORE enabling integration: python -m app.telegram_migrate."""
import os
from sqlalchemy import select
from app import models  # Register jobs/files for foreign-key resolution.
from app.database import engine, SessionLocal
from app.telegram_models import TelegramUser, TelegramRequest, TelegramRuntime


def migrate():
    # checkfirst is repeatable; single operator runs migrations, never concurrent startup.
    for table in (TelegramUser.__table__, TelegramRequest.__table__, TelegramRuntime.__table__):
        table.create(engine, checkfirst=True)
    ids = {int(value.strip()) for value in os.getenv('TELEGRAM_ALLOWED_USERS', '').split(',') if value.strip()}
    if any(value <= 0 or value > 2**63 - 1 for value in ids):
        raise ValueError('Invalid Telegram allowlist')
    with SessionLocal.begin() as db:
        if db.get(TelegramRuntime, 1) is None:
            db.add(TelegramRuntime(id=1))
        for user in db.scalars(select(TelegramUser)):
            user.enabled = user.telegram_id in ids
        for value in ids:
            if db.get(TelegramUser, value) is None:
                db.add(TelegramUser(telegram_id=value, enabled=True))


if __name__ == '__main__':
    migrate()
    print('Telegram migration completed')
