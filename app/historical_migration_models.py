from __future__ import annotations

from datetime import datetime
from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class HistoricalEntityMap(Base):
    """Mapeamento persistente e auditável de IDs do legado para o servidor.

    Nunca presume que legacy_id == central_id. Os IDs do PostgreSQL pertencem
    ao banco central, inclusive quando já são utilizados pelos Acordos.
    """
    __tablename__ = "historical_entity_maps"
    __table_args__ = (
        UniqueConstraint(
            "source_code", "entity_type", "legacy_id",
            name="uq_historical_source_entity_legacy_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_code: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(70), index=True)
    legacy_id: Mapped[int] = mapped_column(Integer)
    central_id: Mapped[int] = mapped_column(Integer, index=True)
    source_digest: Mapped[str] = mapped_column(String(64))
    match_method: Mapped[str] = mapped_column(String(70))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
