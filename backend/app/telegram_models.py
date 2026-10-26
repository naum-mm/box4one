"""Additive integration tables; existing jobs/files are never altered."""
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class TelegramUser(Base):
    __tablename__ = 'telegram_users'
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class TelegramRequest(Base):
    __tablename__ = 'telegram_requests'
    __table_args__ = (UniqueConstraint('telegram_id', 'source_message_id'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(ForeignKey('telegram_users.telegram_id'), index=True)
    source_message_id: Mapped[int] = mapped_column(BigInteger)
    source_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    job_id: Mapped[int | None] = mapped_column(ForeignKey('jobs.id'), unique=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_text: Mapped[str] = mapped_column(String(512), default='')
    terminal_notified: Mapped[bool] = mapped_column(Boolean, default=False)


class TelegramRuntime(Base):
    __tablename__ = 'telegram_runtime'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    owner: Mapped[str] = mapped_column(String(32), default='')
    lease_until: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    offset: Mapped[int] = mapped_column(BigInteger, default=0)


class TelegramActivation(Base):
    __tablename__ = 'telegram_activations'
    telegram_id: Mapped[int] = mapped_column(ForeignKey('telegram_users.telegram_id'), primary_key=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime)
    window_started: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class TelegramDelivery(Base):
    __tablename__ = 'telegram_deliveries'
    telegram_id: Mapped[int] = mapped_column(ForeignKey('telegram_users.telegram_id'), primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey('files.id', ondelete='CASCADE'), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default='sending')
    claim: Mapped[str] = mapped_column(String(32))
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
