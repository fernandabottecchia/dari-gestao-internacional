from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from sqlalchemy import String, Integer, ForeignKey, Date, DateTime, Text, UniqueConstraint, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base



class UserAccount(Base):
    """Conta local e permissões do MVP.

    As senhas são armazenadas somente como hash PBKDF2; a senha original nunca
    é recuperável pela interface. Administradores ignoram as restrições por aba.
    """
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[Optional[str]] = mapped_column(String(140), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    can_view_dashboard: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_view_incoming: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_edit_incoming: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_view_outgoing: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_edit_outgoing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    can_view_acordos: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_edit_acordos: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

class Country(Base):
    __tablename__ = "countries"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    universities: Mapped[list["University"]] = relationship(back_populates="country")

class Course(Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, index=True)

class University(Base):
    __tablename__ = "universities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    acronym: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    country_id: Mapped[Optional[int]] = mapped_column(ForeignKey("countries.id"), nullable=True)
    country: Mapped[Optional[Country]] = relationship(back_populates="universities")

class Coordinator(Base):
    __tablename__ = "coordinators"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)

class Program(Base):
    __tablename__ = "programs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    code: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

class Semester(Base):
    __tablename__ = "semesters"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    year: Mapped[int] = mapped_column(Integer, index=True)
    term: Mapped[int] = mapped_column(Integer)

class Student(Base):
    __tablename__ = "students"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(220), index=True)
    email: Mapped[Optional[str]] = mapped_column(String(180), nullable=True, index=True)
    dre: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    passport: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    nationality: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    mobilities: Mapped[list["Mobility"]] = relationship(back_populates="student")

class Mobility(Base):
    __tablename__ = "mobilities"
    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[str] = mapped_column(String(12), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"), index=True)
    course_id: Mapped[Optional[int]] = mapped_column(ForeignKey("courses.id"), nullable=True)
    program_id: Mapped[Optional[int]] = mapped_column(ForeignKey("programs.id"), nullable=True)
    coordinator_id: Mapped[Optional[int]] = mapped_column(ForeignKey("coordinators.id"), nullable=True)
    home_university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id"), nullable=True)
    destination_university_id: Mapped[Optional[int]] = mapped_column(ForeignKey("universities.id"), nullable=True)
    start_semester_id: Mapped[Optional[int]] = mapped_column(ForeignKey("semesters.id"), nullable=True, index=True)
    end_semester_id: Mapped[Optional[int]] = mapped_column(ForeignKey("semesters.id"), nullable=True)
    mobility_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    duration_semesters: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(140), nullable=True, index=True)
    scholarship: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    extension: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    student: Mapped[Student] = relationship(back_populates="mobilities")
    course: Mapped[Optional[Course]] = relationship()
    program: Mapped[Optional[Program]] = relationship()
    coordinator: Mapped[Optional[Coordinator]] = relationship()
    home_university: Mapped[Optional[University]] = relationship(foreign_keys=[home_university_id])
    destination_university: Mapped[Optional[University]] = relationship(foreign_keys=[destination_university_id])
    start_semester: Mapped[Optional[Semester]] = relationship(foreign_keys=[start_semester_id])
    end_semester: Mapped[Optional[Semester]] = relationship(foreign_keys=[end_semester_id])
    incoming_detail: Mapped[Optional["IncomingDetail"]] = relationship(back_populates="mobility", uselist=False, cascade="all, delete-orphan")
    outgoing_detail: Mapped[Optional["OutgoingDetail"]] = relationship(back_populates="mobility", uselist=False, cascade="all, delete-orphan")

class IncomingDetail(Base):
    __tablename__ = "incoming_details"
    id: Mapped[int] = mapped_column(primary_key=True)
    mobility_id: Mapped[int] = mapped_column(ForeignKey("mobilities.id"), unique=True)
    candidate_document: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    polimail: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    coordination_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    intranet_access: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    identification: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    dari_form: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    study_plan: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    responsible_name: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    responsible_email: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    mobility: Mapped[Mobility] = relationship(back_populates="incoming_detail")

class OutgoingDetail(Base):
    __tablename__ = "outgoing_details"
    id: Mapped[int] = mapped_column(primary_key=True)
    mobility_id: Mapped[int] = mapped_column(ForeignKey("mobilities.id"), unique=True)
    registration_semester: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    expected_return: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    mobility: Mapped[Mobility] = relationship(back_populates="outgoing_detail")


class Application(Base):
    """Candidatura recebida de formulário antes de virar cadastro oficial.

    A tabela é deliberadamente separada de Student/Mobility: uma resposta só
    entra no banco operacional depois da decisão explícita da equipe da DARI.
    raw_payload_json preserva integralmente os campos específicos de cada
    formulário, inclusive quando o esquema do Google Form mudar no futuro.
    """
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("source", "source_record_id", name="uq_application_source_record"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    direction: Mapped[str] = mapped_column(String(12), index=True)
    source: Mapped[str] = mapped_column(String(80), default="google_forms", index=True)
    source_record_id: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)

    # Campos centrais usados para triagem, busca e promoção para o cadastro oficial.
    first_name: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    surnames: Mapped[Optional[str]] = mapped_column(String(220), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(180), nullable=True, index=True)
    dre: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    passport: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    country_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    academic_semester: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    application_type: Mapped[Optional[str]] = mapped_column(String(140), nullable=True, index=True)
    course_name: Mapped[Optional[str]] = mapped_column(String(220), nullable=True)
    institution_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # O PDF pode vir como link somente-leitura do Drive ou como cópia local
    # gerada futuramente pelo processo de sincronização.
    document_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    document_local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Todos os demais campos do formulário são mantidos sem perda.
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    mobility_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mobilities.id"), nullable=True, index=True)
    mobility: Mapped[Optional["Mobility"]] = relationship()

class Agreement(Base):
    __tablename__ = "agreements"
    id: Mapped[int] = mapped_column(primary_key=True)
    university_id: Mapped[int] = mapped_column(ForeignKey("universities.id"), index=True)
    agreement_type: Mapped[str] = mapped_column(String(150))
    sig_number: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    sei_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    progress: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    ufrj_stage: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    signed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    validity_years: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expiry_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    status: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    drive_folder_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ufrj_coordinator: Mapped[Optional[str]] = mapped_column(String(220), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    university: Mapped[University] = relationship()
    actions: Mapped[list["AgreementAction"]] = relationship(back_populates="agreement", cascade="all, delete-orphan", order_by="AgreementAction.id")

class AgreementAction(Base):
    __tablename__ = "agreement_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"), index=True)
    last_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_action_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    next_step: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deadline_text: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action_source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False, index=True)
    # ID da mesma ação no PostgreSQL central quando este registro for apenas
    # uma cópia/cache local. No banco central o campo permanece nulo.
    central_action_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    agreement: Mapped[Agreement] = relationship(back_populates="actions")

class IntegrationSource(Base):
    """Fonte externa usada somente para leitura/sincronização."""
    __tablename__ = "integration_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(220))
    source_type: Mapped[str] = mapped_column(String(80), index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    sheet_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    read_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_mode: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    last_sync_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_item_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_created: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_updated: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_archived: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class AgreementSourceRecord(Base):
    """Rastreabilidade entre um acordo local e o registro externo de origem.

    O registro externo nunca é alterado pelo Sistema DARI. Quando uma linha
    deixa de existir na fonte, source_deleted é marcado sem apagar o histórico
    local.
    """
    __tablename__ = "agreement_source_records"
    __table_args__ = (
        UniqueConstraint("source_id", "source_key", name="uq_agreement_source_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("integration_sources.id"), index=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"), index=True)
    source_key: Mapped[str] = mapped_column(String(500))
    identity_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    source_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    action_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_rows_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)



# ----------------------- Incoming: importacao manual integral -----------------------
# Tabelas de STAGING, separadas de Student/Mobility ate conciliacao aprovada.
# Preservam as abas/tabelas e campos originais, com rastreabilidade.

class IncomingSourceBatch(Base):
    __tablename__ = "incoming_source_batches"
    id: Mapped[int] = mapped_column(primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    imported_rows: Mapped[int] = mapped_column(Integer)
    imported_sheets: Mapped[int] = mapped_column(Integer)
    imported_tables: Mapped[int] = mapped_column(Integer)
    read_only_source: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    operational_promotion: Mapped[str] = mapped_column(String(40), default="not_reviewed")
    technical_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class IncomingSourceWorksheet(Base):
    __tablename__ = "incoming_source_worksheets"
    __table_args__ = (UniqueConstraint("batch_id", "sheet_name", name="uq_incoming_sheet_per_batch"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_batches.id"), index=True)
    sheet_name: Mapped[str] = mapped_column(String(130))
    sheet_order: Mapped[int] = mapped_column(Integer)
    sheet_kind: Mapped[str] = mapped_column(String(100))
    max_row: Mapped[int] = mapped_column(Integer)
    max_col: Mapped[int] = mapped_column(Integer)
    nonempty_rows: Mapped[int] = mapped_column(Integer)
    formula_cells: Mapped[int] = mapped_column(Integer)
    hyperlink_cells: Mapped[int] = mapped_column(Integer)
    validation_rules: Mapped[int] = mapped_column(Integer)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    original_headers_json: Mapped[str] = mapped_column(Text)


class IncomingSourceTable(Base):
    __tablename__ = "incoming_source_tables"
    __table_args__ = (UniqueConstraint("batch_id", "table_name", name="uq_incoming_table_per_batch"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_batches.id"), index=True)
    table_name: Mapped[str] = mapped_column(String(110))
    sheet_name: Mapped[str] = mapped_column(String(130))
    sheet_range: Mapped[str] = mapped_column(String(60))
    column_labels_json: Mapped[str] = mapped_column(Text)
    is_header_only: Mapped[bool] = mapped_column(Boolean, default=False)


class IncomingSourceRow(Base):
    __tablename__ = "incoming_source_rows"
    __table_args__ = (UniqueConstraint("batch_id", "sheet_name", "source_row", name="uq_incoming_row_position"),
                      UniqueConstraint("batch_id", "source_key", name="uq_incoming_source_key_batch"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_batches.id"), index=True)
    sheet_name: Mapped[str] = mapped_column(String(130), index=True)
    source_row: Mapped[int] = mapped_column(Integer)
    row_kind: Mapped[str] = mapped_column(String(60), index=True)
    source_key: Mapped[str] = mapped_column(String(255), index=True)
    row_sha256: Mapped[str] = mapped_column(String(64), index=True)
    raw_values_json: Mapped[str] = mapped_column(Text)
    field_values_json: Mapped[str] = mapped_column(Text)
    normalized_json: Mapped[str] = mapped_column(Text)
    hyperlink_json: Mapped[str] = mapped_column(Text)
    formulas_json: Mapped[str] = mapped_column(Text)
    nonempty: Mapped[bool] = mapped_column(Boolean)
    redacted_credentials: Mapped[int] = mapped_column(Integer, default=0)
    review_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("students.id"), nullable=True)
    mobility_id: Mapped[Optional[int]] = mapped_column(ForeignKey("mobilities.id"), nullable=True)


class IncomingSourceAnnotation(Base):
    __tablename__ = "incoming_source_annotations"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_batches.id"), index=True)
    sheet_name: Mapped[str] = mapped_column(String(130))
    cell: Mapped[str] = mapped_column(String(30))
    annotation_type: Mapped[str] = mapped_column(String(50))
    author: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_part: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class IncomingCountryAliasProposal(Base):
    __tablename__ = "incoming_country_alias_proposals"
    __table_args__ = (UniqueConstraint("batch_id", "source_value", name="uq_incoming_country_alias"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_batches.id"), index=True)
    source_value: Mapped[str] = mapped_column(String(200))
    canonical_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer)
    review_required: Mapped[bool] = mapped_column(Boolean, default=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class IncomingUniversityVariant(Base):
    __tablename__ = "incoming_university_variants"
    __table_args__ = (UniqueConstraint("batch_id", "source_value", name="uq_incoming_university_variant"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_batches.id"), index=True)
    source_value: Mapped[str] = mapped_column(String(500))
    normalized_text: Mapped[str] = mapped_column(String(500))
    occurrences: Mapped[int] = mapped_column(Integer)
    example_sheet: Mapped[str] = mapped_column(String(130))
    legacy_exact_match: Mapped[bool] = mapped_column(Boolean, default=False)
    review_required: Mapped[bool] = mapped_column(Boolean, default=True)


class IncomingImportIssue(Base):
    __tablename__ = "incoming_import_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("incoming_source_batches.id"), index=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    severity: Mapped[str] = mapped_column(String(40))
    sheet_name: Mapped[Optional[str]] = mapped_column(String(130), nullable=True)
    source_row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    details: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
