"""Additive quota bookkeeping; existing jobs/files remain unchanged."""
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class WebClient(Base):
    __tablename__ = 'web_clients'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)


class WebRequest(Base):
    __tablename__ = 'web_requests'
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id', ondelete='CASCADE'), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey('web_clients.id'), index=True)
