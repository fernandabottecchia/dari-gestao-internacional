from __future__ import annotations

from datetime import datetime, date
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .agreement_progress import normalize_progress
from .models import (
    Agreement,
    AgreementAction,
    AgreementSourceRecord,
    Country,
    IntegrationSource,
    University,
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _canonical_text(value: Any) -> str | None:
    """Normaliza apenas diferenças técnicas de representação do mesmo texto."""
    value = _text(value)
    if value is None:
        return None
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _norm(value: Any) -> str:
    value = _text(value) or ""
    value = value.replace("’", "'").replace("“", '"').replace("”", '"')
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"\s+", " ", value).strip().casefold()
    return value


def _date(value: Any) -> date | None:
    raw = _text(value)
    if not raw:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass

    # Snapshots/exports podem representar a mesma data como timestamp ISO,
    # por exemplo 2030-09-02T00:00:00.000Z.
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _canonical_date(value: Any) -> str | None:
    parsed = _date(value)
    if parsed:
        return parsed.isoformat()
    return _canonical_text(value)


def _canonical_validity(value: Any) -> int | str | None:
    raw = _text(value)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


def _canonical_drive_url(value: Any) -> str | None:
    raw = _text(value)
    if not raw:
        return None

    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw

    host = (parsed.hostname or "").casefold()
    if host == "drive.google.com" or host.endswith(".drive.google.com"):
        path = parsed.path.rstrip("/") or "/"
        return urlunsplit((parsed.scheme or "https", parsed.netloc, path, "", ""))

    return raw


def _canonical_source_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Representação estável usada apenas para detectar mudança real da fonte.

    Não altera o payload bruto salvo para auditoria. Assim:
    - 5 e "5" em Vigência são equivalentes;
    - YYYY-MM-DD e o mesmo dia em timestamp ISO são equivalentes;
    - CRLF e LF são equivalentes;
    - parâmetros de interface em URLs do Drive não viram "alteração".
    """
    result: dict[str, Any] = {}

    for key, value in record.items():
        if key in {"source_rows", "source_occurrences", "action"}:
            continue

        if key in {"signed_date", "expiry_date"}:
            result[key] = _canonical_date(value)
        elif key == "validity_years":
            result[key] = _canonical_validity(value)
        elif key == "drive_folder_url":
            result[key] = _canonical_drive_url(value)
        elif isinstance(value, str):
            result[key] = _canonical_text(value)
        else:
            result[key] = value

    return result


def _json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity_key(record: dict[str, Any]) -> str:
    return f"u:{_norm(record.get('university'))}|t:{_norm(record.get('agreement_type'))}"


def _country(db: Session, name: str | None) -> Country | None:
    if not name:
        return None
    countries = db.scalars(select(Country)).all()
    wanted = _norm(name)
    for country in countries:
        if _norm(country.name) == wanted:
            return country
    country = Country(name=name.strip())
    db.add(country)
    db.flush()
    return country


def _university(db: Session, record: dict[str, Any]) -> University:
    name = _text(record.get("university"))
    if not name:
        raise ValueError("Registro de acordo sem universidade.")
    acronym = _text(record.get("acronym"))
    country = _country(db, _text(record.get("country")))
    wanted = _norm(name)
    # DARI_V53_20_UNIVERSITY_NAMES: uma nova importação da planilha não cria
    # outra instituição quando o nome anterior já tem alias inequívoco.
    from .university_reference_v53_20 import resolve_alias_orm
    canonical_university = resolve_alias_orm(db,name)
    if canonical_university is not None:
        if country and not canonical_university.country_id:
            canonical_university.country_id = country.id
        return canonical_university

    universities = db.scalars(select(University)).all()
    exact = [u for u in universities if _norm(u.name) == wanted]
    if len(exact) == 1:
        u = exact[0]
    elif not exact and acronym:
        by_acronym = [u for u in universities if _norm(u.acronym) == _norm(acronym)]
        if len(by_acronym) == 1:
            u = by_acronym[0]
        else:
            u = University(name=name)
            db.add(u)
            db.flush()
    elif exact:
        # University.name é UNIQUE no esquema atual, mas mantemos a escolha explícita.
        u = exact[0]
    else:
        u = University(name=name)
        db.add(u)
        db.flush()

    if acronym and not u.acronym:
        u.acronym = acronym
    if country and not u.country_id:
        u.country_id = country.id
    return u


def _apply_agreement_fields(agreement: Agreement, university: University, record: dict[str, Any]) -> None:
    agreement.university_id = university.id
    agreement.agreement_type = _text(record.get("agreement_type")) or "Não informado"
    agreement.sig_number = _text(record.get("sig_number"))
    agreement.sei_number = _text(record.get("sei_number"))
    agreement.progress = normalize_progress(record.get("progress"))
    agreement.ufrj_stage = _text(record.get("ufrj_stage"))
    agreement.signed_date = _date(record.get("signed_date"))
    validity = record.get("validity_years")
    try:
        agreement.validity_years = int(validity) if validity not in (None, "") else None
    except (TypeError, ValueError):
        agreement.validity_years = None
    agreement.expiry_date = _date(record.get("expiry_date"))
    agreement.status = _text(record.get("status"))
    agreement.drive_folder_url = _canonical_drive_url(record.get("drive_folder_url"))
    agreement.ufrj_coordinator = _text(record.get("ufrj_coordinator"))
    agreement.notes = _canonical_text(record.get("notes"))


def _find_existing_agreement(db: Session, source: IntegrationSource, record: dict[str, Any]) -> Agreement | None:
    source_key = record["source_key"]
    trace = db.scalar(
        select(AgreementSourceRecord).where(
            AgreementSourceRecord.source_id == source.id,
            AgreementSourceRecord.source_key == source_key,
        )
    )
    if trace:
        return db.get(Agreement, trace.agreement_id)

    # Se um identificador externo (por exemplo SIG+) for corrigido, tentamos
    # conservar o mesmo acordo usando a identidade universidade + tipo.
    ident = _identity_key(record)
    traces = db.scalars(
        select(AgreementSourceRecord).where(
            AgreementSourceRecord.source_id == source.id,
            AgreementSourceRecord.identity_key == ident,
            AgreementSourceRecord.source_deleted.is_(False),
        )
    ).all()
    if len(traces) == 1:
        traces[0].source_key = source_key
        return db.get(Agreement, traces[0].agreement_id)

    sig = _text(record.get("sig_number"))
    if sig:
        candidates = db.scalars(select(Agreement).where(Agreement.sig_number == sig)).all()
        if len(candidates) == 1:
            return candidates[0]

    # Fallback útil para a primeira migração sobre um banco que já possua
    # acordos cadastrados manualmente, sem rastreabilidade da planilha.
    univ = _norm(record.get("university"))
    typ = _norm(record.get("agreement_type"))
    candidates = db.scalars(select(Agreement)).all()
    matched = [a for a in candidates if _norm(a.university.name if a.university else "") == univ and _norm(a.agreement_type) == typ]
    if len(matched) == 1:
        return matched[0]
    return None


def _sync_action(db: Session, agreement: Agreement, record: dict[str, Any], previous_action_hash: str | None) -> str | None:
    """Sincroniza Controle_Ações preservando histórico.

    Durante a transição:
    - Última ação = fato já realizado (histórico).
    - Próximo passo = item A fazer.
    - se Próximo passo for substituído na planilha, o valor anterior passa
      a concluído e o novo valor vira um novo A fazer.
    - se Próximo passo for limpo, o item anterior é concluído e nenhum
      item externo permanece aberto.
    """
    action = record.get("action")
    if not action:
        return None

    action_payload = {
        "last_action": _canonical_text(action.get("last_action")),
        "last_action_date": _canonical_date(action.get("last_action_date")),
        "next_step": _canonical_text(action.get("next_step")),
        "deadline_text": _canonical_text(action.get("deadline_text")),
        "notes": _canonical_text(action.get("notes")),
    }
    action_hash = _json_hash(action_payload)
    if action_hash == previous_action_hash:
        return action_hash

    new_last_action = action_payload["last_action"]
    new_last_date = _date(action_payload["last_action_date"])
    new_next_step = action_payload["next_step"]

    external_actions = db.scalars(
        select(AgreementAction)
        .where(
            AgreementAction.agreement_id == agreement.id,
            AgreementAction.action_source == "external",
        )
        .order_by(AgreementAction.id.desc())
    ).all()

    # Última ação é histórica.
    if new_last_action:
        has_same_last_action = any(
            _norm(item.last_action) == _norm(new_last_action)
            and item.last_action_date == new_last_date
            for item in external_actions
        )
        if not has_same_last_action:
            db.add(AgreementAction(
                agreement_id=agreement.id,
                last_action=new_last_action,
                last_action_date=new_last_date,
                next_step=None,
                deadline_text=None,
                notes=None,
                action_source="external",
                is_completed=True,
                completed_at=datetime.utcnow(),
            ))

    # Próximos passos externos que ainda estão abertos.
    open_external = [
        item for item in external_actions
        if not item.is_completed and _text(item.next_step)
    ]

    same_open = (
        next(
            (item for item in open_external if _norm(item.next_step) == _norm(new_next_step)),
            None,
        )
        if new_next_step
        else None
    )

    if same_open:
        # Mesmo conteúdo: somente prazo/observação foram alterados.
        same_open.deadline_text = action_payload["deadline_text"]
        same_open.notes = action_payload["notes"]
    else:
        # Conteúdo da célula mudou ou foi apagado: o anterior está concluído.
        for previous in open_external:
            previous.is_completed = True
            previous.completed_at = datetime.utcnow()

        if new_next_step:
            db.add(AgreementAction(
                agreement_id=agreement.id,
                last_action=None,
                last_action_date=None,
                next_step=new_next_step,
                deadline_text=action_payload["deadline_text"],
                notes=action_payload["notes"],
                action_source="external",
                is_completed=False,
                created_at=datetime.utcnow(),
            ))

    return action_hash


def sync_agreements_from_payload(db: Session, payload: dict[str, Any], *, commit: bool = True) -> dict[str, int]:
    """Sincroniza Acordos a partir de uma leitura completa e somente-leitura.

    A função é independente da origem concreta. Hoje ela recebe o snapshot do
    Google Sheet. Futuramente o adaptador Google Sheets do servidor central
    alimentará esta mesma função com os valores lidos pela API read-only.
    """
    source_cfg = payload["source"]
    source = db.scalar(select(IntegrationSource).where(IntegrationSource.code == source_cfg["code"]))
    if not source:
        source = IntegrationSource(
            code=source_cfg["code"],
            name=source_cfg["name"],
            source_type=source_cfg["source_type"],
        )
        db.add(source)
        db.flush()

    source.name = source_cfg["name"]
    source.source_type = source_cfg["source_type"]
    source.external_id = source_cfg.get("external_id")
    source.sheet_name = source_cfg.get("sheet_name")
    source.read_only = True
    source.sync_mode = source_cfg.get("sync_mode") or "snapshot_manual"
    catalogs = dict(payload.get("catalogs", {}))
    if isinstance(catalogs.get("progress"), list):
        catalogs["progress"] = list(dict.fromkeys(
            normalize_progress(value) for value in catalogs["progress"] if normalize_progress(value)
        ))
    source.config_json = json.dumps(catalogs, ensure_ascii=False, sort_keys=True)

    seen_trace_ids: set[int] = set()
    created = updated = unchanged = restored = 0

    for record in payload.get("records", []):
        source_hash_payload = _canonical_source_payload(record)
        source_hash = _json_hash(source_hash_payload)
        agreement = _find_existing_agreement(db, source, record)
        is_new = agreement is None
        if agreement is None:
            university = _university(db, record)
            agreement = Agreement(university_id=university.id, agreement_type=_text(record.get("agreement_type")) or "Não informado")
            db.add(agreement)
            db.flush()
        else:
            university = _university(db, record)

        trace = db.scalar(
            select(AgreementSourceRecord).where(
                AgreementSourceRecord.source_id == source.id,
                AgreementSourceRecord.agreement_id == agreement.id,
            )
        )
        if not trace:
            trace = AgreementSourceRecord(
                source_id=source.id,
                agreement_id=agreement.id,
                source_key=record["source_key"],
                identity_key=_identity_key(record),
                first_seen_at=datetime.utcnow(),
                last_seen_at=datetime.utcnow(),
            )
            db.add(trace)
            db.flush()

        previously_deleted = bool(trace.source_deleted)
        before_hash = trace.source_hash
        before_semantic_hash = before_hash

        if trace.source_payload_json:
            try:
                previous_payload = json.loads(trace.source_payload_json)
                before_semantic_hash = _json_hash(_canonical_source_payload(previous_payload))
            except (TypeError, ValueError, json.JSONDecodeError):
                before_semantic_hash = before_hash

        from .editing_v53_30 import edited
        if not edited(db,"agreement",agreement.id):
            _apply_agreement_fields(agreement, university, record)
        action_hash = trace.action_hash if edited(db,'agreement_actions',agreement.id) else _sync_action(db, agreement, record, trace.action_hash)

        trace.source_key = record["source_key"]
        trace.identity_key = _identity_key(record)
        trace.source_hash = source_hash
        trace.action_hash = action_hash
        trace.source_occurrences = int(record.get("source_occurrences") or 1)
        trace.source_rows_json = json.dumps(record.get("source_rows") or [], ensure_ascii=False)
        trace.source_payload_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
        trace.source_deleted = False
        trace.deleted_at = None
        trace.last_seen_at = datetime.utcnow()
        seen_trace_ids.add(trace.id)

        if is_new:
            created += 1
        elif previously_deleted:
            restored += 1
            updated += 1
        elif before_semantic_hash != source_hash:
            updated += 1
        else:
            unchanged += 1

    archived = 0
    existing_traces = db.scalars(select(AgreementSourceRecord).where(AgreementSourceRecord.source_id == source.id)).all()
    for trace in existing_traces:
        if trace.id not in seen_trace_ids and not trace.source_deleted:
            trace.source_deleted = True
            trace.deleted_at = datetime.utcnow()
            archived += 1

    source.last_sync_at = datetime.utcnow()
    source.last_sync_status = "ok"
    source.last_item_count = len(payload.get("records", []))
    source.last_created = created
    source.last_updated = updated
    source.last_archived = archived
    duplicate_count = int(payload.get("stats", {}).get("exact_duplicate_rows_collapsed") or 0)
    source.last_sync_message = (
        f"Sincronização concluída: {len(payload.get('records', []))} acordo(s) lógico(s); "
        f"{created} novo(s), {updated} atualizado(s), {archived} arquivado(s). "
        f"{duplicate_count} linha(s) duplicada(s) consolidada(s)."
    )

    if commit:
        db.commit()

    return {
        "source_rows": int(payload.get("stats", {}).get("source_rows") or len(payload.get("records", []))),
        "logical_records": len(payload.get("records", [])),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "restored": restored,
        "archived": archived,
        "duplicates_collapsed": duplicate_count,
    }


def active_source_record_map(db: Session, agreement_ids: list[int]) -> dict[int, AgreementSourceRecord]:
    if not agreement_ids:
        return {}
    traces = db.scalars(
        select(AgreementSourceRecord).where(
            AgreementSourceRecord.agreement_id.in_(agreement_ids),
            AgreementSourceRecord.source_deleted.is_(False),
        )
    ).all()
    return {trace.agreement_id: trace for trace in traces}


def source_managed_read_only(db: Session, agreement_id: int) -> bool:
    trace = db.scalar(
        select(AgreementSourceRecord).where(
            AgreementSourceRecord.agreement_id == agreement_id,
            AgreementSourceRecord.source_deleted.is_(False),
        ).limit(1)
    )
    if not trace:
        return False
    source = db.get(IntegrationSource, trace.source_id)
    return bool(source and source.read_only)
