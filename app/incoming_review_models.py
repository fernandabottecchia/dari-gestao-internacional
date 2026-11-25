from __future__ import annotations
from datetime import date
from typing import Optional
from sqlalchemy import String, Integer, ForeignKey, Date, Text, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base

# ---------- Incoming: reconciliação e evidências NÃO operacionais ----------
# Estas tabelas registram evidências da fonte e fotografias históricas. Não
# promovem automaticamente dados para students/mobilities/incoming_details.
class IncomingFieldEvidence(Base):
    __tablename__ = "incoming_field_evidence"
    __table_args__ = (
        UniqueConstraint("source_row_id", "field_key", name="uq_incoming_field_per_row"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_row_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_rows.id"), index=True)
    field_key: Mapped[str] = mapped_column(String(100))
    value_json: Mapped[str] = mapped_column(Text)
    proposed_destination: Mapped[str] = mapped_column(String(170))
    semantic_group: Mapped[str] = mapped_column(String(70), index=True)
    requires_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    review_status: Mapped[str] = mapped_column(String(45), default="pending", index=True)

class IncomingSemesterActivityEvidence(Base):
    __tablename__ = "incoming_semester_activity_evidence"
    __table_args__ = (
        UniqueConstraint("source_row_id", name="uq_incoming_active_source_row"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source_row_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_rows.id"), index=True)
    reference_semester: Mapped[str] = mapped_column(String(12), index=True)
    evidence_kind: Mapped[str] = mapped_column(String(60), default="listed_in_active_snapshot")
    confirmation: Mapped[str] = mapped_column(String(45), default="historical_source_claim_unverified")
    # Somente depois de revisar identidade e abrangência do período.
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id"), nullable=True)
    mobility_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mobilities.id"), nullable=True)

class IncomingOperationalSupplement(Base):
    """Campos próprios do módulo Incoming; criados vazios nesta etapa.

    Sua futura promoção deve ser revisada e gravada no PostgreSQL central,
    NUNCA preenchida automaticamente a partir de um alias/campo ambíguo.
    """
    __tablename__ = "incoming_operational_supplements"
    id: Mapped[int] = mapped_column(primary_key=True)
    mobility_id: Mapped[int] = mapped_column(ForeignKey("mobilities.id"), unique=True, index=True)
    alternate_email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    coordination_opinion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    letter_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registration_dre_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internal_process_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_arrival_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    operational_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internship_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    administrative_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_arrival: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    confirmed_arrival: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    expected_departure: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    confirmed_departure: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
