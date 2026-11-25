from .editing_v53_30 import sort_context, mark as mark30, edited as edited30, snapshot as snapshot30, source_fields as source_fields30, valid_semester
from datetime import date, datetime, timedelta
import json
import math
import base64
import hashlib
import hmac
import os
import secrets
import shutil
from pathlib import Path
from urllib.parse import quote
from fastapi import FastAPI, Depends, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, or_, cast, String, text, column, table
from sqlalchemy.orm import Session, joinedload, selectinload

from .database import Base, engine, get_db, SessionLocal
from .form_bridge_v53_48 import (FormResponse, FormSource, FormFile, application_extras,
    mobility_extras, start_poller as start_form_poller48, stop_poller as stop_form_poller48,
    router as form_router48, ensure_form_source_mode_schema, register_incoming_source_once,
    reconcile_incoming_types48_3, FormApprovedExtras, register_outgoing_sources48_12,
    editable_form_cards48_12, save_form_cards48_12)
from . import document_repository_v53_47 as document_repo
from .document_sync_v53_47 import synchronize as synchronize_documents
from .university_history import matching_universities, matching_source_labels
from .mobility_schedule_v53_40 import context as schedule_context40, prepare as prepare_schedule40, save as save_schedule40
from .academic_calendar import read_cache, save_cache, replace_cache, validate_term
from .models import (
    Student, Mobility, Semester, University, Agreement, AgreementAction,
    Course, Program, Coordinator, IncomingDetail, OutgoingDetail, Country, Application, UserAccount,
    IntegrationSource, AgreementSourceRecord,
    IncomingSourceBatch, IncomingSourceWorksheet, IncomingSourceTable,
    IncomingSourceRow, IncomingSourceAnnotation,
)
from .hosting import settings, security_middleware, validate_database, issue_session, read_session, password_ok
from .document_generation import generate_acceptance_docx, generate_acceptance_pdf, safe_filename
from .reporting import (
    DETAIL_REPORT_DEFINITIONS, dashboard_data, build_report, report_to_pdf, report_to_xlsx,
    filter_query_string, get_filter_options, mobility_detail_rows, agreement_detail_rows,
    MOBILITY_DETAIL_COLUMNS, AGREEMENT_DETAIL_COLUMNS
)
from .agreement_sync import active_source_record_map, source_managed_read_only
from .agreement_progress import normalize_progress, is_pending_progress


# DARI_V53_16_WORKFLOW
from .mobility_workflow_v53_16 import (ensure_schema as _workflow_ensure_schema,
    state_map as _workflow_state_map, state_of as _workflow_state_of,
    change_state as _workflow_change_state)

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="DARI - Gestão de Mobilidade Internacional", version="53.49.0-rc1", docs_url=None, redoc_url=None, openapi_url=None)
from starlette.middleware.trustedhost import TrustedHostMiddleware
from urllib.parse import urlsplit
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[urlsplit(settings.public_url).hostname])
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

SYSTEM_BUILD = "53.49.0-rc1 • preparação para hospedagem TIC"
SYSTEM_ARCHITECTURE_STATUS = "Aplicação web com banco único PostgreSQL. Acesso pelo navegador com autenticação."
DIRECTOR_USERNAME = "dari"
DEMO_SECRET = settings.secret
AUTH_COOKIE = "dari_session"
PASSWORD_ITERATIONS = 260_000


def _ensure_agreement_action_schema():
    """Compatibilidade leve para bancos SQLite já existentes do MVP."""
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info('agreement_actions')")}
        statements = []
        if "action_source" not in cols:
            statements.append("ALTER TABLE agreement_actions ADD COLUMN action_source VARCHAR(20) NOT NULL DEFAULT 'manual'")
        if "central_action_id" not in cols:
            statements.append("ALTER TABLE agreement_actions ADD COLUMN central_action_id INTEGER")
        if "is_completed" not in cols:
            statements.append("ALTER TABLE agreement_actions ADD COLUMN is_completed BOOLEAN NOT NULL DEFAULT 0")
        if "created_at" not in cols:
            statements.append("ALTER TABLE agreement_actions ADD COLUMN created_at DATETIME")
        if "completed_at" not in cols:
            statements.append("ALTER TABLE agreement_actions ADD COLUMN completed_at DATETIME")
        for stmt in statements:
            conn.exec_driver_sql(stmt)


def _sorted_actions(actions: list[AgreementAction]) -> list[AgreementAction]:
    def action_key(action: AgreementAction):
        dt = action.last_action_date or action.completed_at or action.created_at
        ordinal = dt.toordinal() if hasattr(dt, "toordinal") else 0
        return (ordinal, action.id or 0)
    return sorted(actions, key=action_key, reverse=True)


def _agreement_action_context(actions: list[AgreementAction]) -> dict:
    """Separa itens a fazer e histórico, permitindo vários itens em aberto."""
    ordered = _sorted_actions(actions)

    open_actions = [
        act for act in ordered
        if not getattr(act, "is_completed", False)
        and (act.next_step or "").strip()
    ]

    history: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    def add_history(
        *,
        text_value,
        display_date,
        sort_at,
        notes_value,
        source_value,
        deadline_value=None,
        action_id=None,
        kind="action",
    ):
        text_clean = (text_value or "").strip()
        if not text_clean:
            return
        if isinstance(display_date, datetime):
            display_date = display_date.date()
        date_key = display_date.isoformat() if hasattr(display_date, "isoformat") else ""
        key = (kind, str(action_id or ""), text_clean.casefold() + "|" + date_key)
        if key in seen:
            return
        seen.add(key)
        history.append({
            "text": text_clean,
            "date": display_date,
            "sort_at": sort_at,
            "notes": (notes_value or "").strip() or None,
            "source": source_value or "manual",
            "deadline": (deadline_value or "").strip() or None,
            "action_id": action_id,
            "kind": kind,
        })

    for act in ordered:
        # A "Última ação" importada já ocorreu: pertence ao histórico.
        add_history(
            text_value=act.last_action,
            display_date=act.last_action_date,
            sort_at=act.last_action_date,
            notes_value=act.notes if not act.next_step else None,
            source_value=act.action_source,
            action_id=act.id,
            kind="last_action",
        )

        # Próximos passos concluídos entram no histórico.
        if getattr(act, "is_completed", False) and (act.next_step or "").strip():
            completed_sort = act.completed_at or act.created_at
            add_history(
                text_value=act.next_step,
                display_date=completed_sort,
                sort_at=completed_sort,
                notes_value=act.notes,
                source_value=act.action_source,
                deadline_value=act.deadline_text,
                action_id=act.id,
                kind="step",
            )

    def history_key(item):
        value = item.get("sort_at")
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time()).timestamp()
        return 0

    # O concluído mais recentemente aparece primeiro.
    history.sort(key=history_key, reverse=True)
    return {"open_actions": open_actions, "history_actions": history}




def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations))
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def _session_token(username: str) -> str:
    with SessionLocal() as db:
        user = db.scalar(select(UserAccount).where(UserAccount.username == username))
        return issue_session(username, user.password_hash)


def _session_username(token: str) -> str | None:
    return read_session(token).get('user')


def _snapshot_user(user: UserAccount) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "is_admin": bool(user.is_admin),
        "can_view_dashboard": bool(user.can_view_dashboard),
        "can_view_incoming": bool(user.can_view_incoming),
        "can_edit_incoming": bool(user.can_edit_incoming),
        "can_view_outgoing": bool(user.can_view_outgoing),
        "can_edit_outgoing": bool(user.can_edit_outgoing),
        "can_view_acordos": bool(user.can_view_acordos),
        "can_edit_acordos": bool(user.can_edit_acordos),
    }


def _user_from_request_cookie(request: Request) -> dict | None:
    username = _session_username(request.cookies.get(AUTH_COOKIE, ""))
    if not username:
        return None
    with SessionLocal() as db:
        user = db.scalar(
            select(UserAccount).where(func.lower(UserAccount.username) == username.lower(), UserAccount.is_active.is_(True)).limit(1)
        )
        return _snapshot_user(user) if user and read_session(request.cookies.get(AUTH_COOKIE, ''), user.password_hash) else None


def _permissions(user: dict | None) -> dict:
    admin = bool(user and user.get("is_admin"))
    return {
        "dashboard": {"view": admin or bool(user and user.get("can_view_dashboard")), "edit": False},
        "incoming": {"view": admin or bool(user and user.get("can_view_incoming")), "edit": admin or bool(user and user.get("can_edit_incoming"))},
        "outgoing": {"view": admin or bool(user and user.get("can_view_outgoing")), "edit": admin or bool(user and user.get("can_edit_outgoing"))},
        "acordos": {"view": admin or bool(user and user.get("can_view_acordos")), "edit": admin or bool(user and user.get("can_edit_acordos"))},
    }


def _can_view(request: Request, area: str) -> bool:
    return bool(_permissions(getattr(request.state, "user", None)).get(area, {}).get("view"))


def _can_edit(request: Request, area: str) -> bool:
    return bool(_permissions(getattr(request.state, "user", None)).get(area, {}).get("edit"))


def _require_view(request: Request, area: str):
    if not _can_view(request, area):
        raise HTTPException(status_code=403, detail="Seu usuário não possui permissão para visualizar esta área.")


def _require_edit(request: Request, area: str):
    if not _can_edit(request, area):
        raise HTTPException(status_code=403, detail="Seu usuário possui acesso somente para visualização nesta área.")


def _require_admin(request: Request):
    user = getattr(request.state, "user", None)
    if not user or not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Somente o administrador pode acessar esta configuração.")


@app.middleware("http")
async def require_login(request: Request, call_next):
    request.state.user = _user_from_request_cookie(request)
    path = request.url.path
    public = path in ("/login", "/health/live", "/health/ready") or path.startswith("/static/")
    if not public and not request.state.user:
        return RedirectResponse(url="/login", status_code=303)
    return await security_middleware(request, call_next)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if getattr(request.state, "user", None):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "error": None},
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    normalized = username.strip()
    user = db.scalar(
        select(UserAccount).where(func.lower(UserAccount.username) == normalized.lower(), UserAccount.is_active.is_(True)).limit(1)
    )
    if not user or not _verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"request": request, "error": "Usuário ou senha inválidos."},
            status_code=401,
        )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=AUTH_COOKIE, value=_session_token(user.username),
        httponly=True, secure=settings.production, samesite="lax", max_age=60 * 60 * 8,
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(AUTH_COOKIE)
    return response


# Exclusao administrativa de processos: o cadastro e seus anexos ficam preservados
# para auditoria e eventual recuperacao por administradores. Nao faz DELETE em
# students, mobilities, candidaturas ou documentos.
_MOBILITY_DELETED_TABLE = 'dari_deleted_mobilities_v53_48_11'


def _ensure_mobility_deletion_schema():
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE IF NOT EXISTS dari_deleted_mobilities_v53_48_11 (
                mobility_id INTEGER PRIMARY KEY,
                deleted_by VARCHAR(120) NOT NULL,
                deleted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def _mobility_not_deleted():
    return Mobility.id.not_in(
        select(column('mobility_id')).select_from(table(_MOBILITY_DELETED_TABLE))
    )


def _mobility_is_deleted(db: Session, mobility_id: int) -> bool:
    return bool(db.scalar(text("""
        SELECT 1 FROM dari_deleted_mobilities_v53_48_11
        WHERE mobility_id = :mid LIMIT 1
    """), {'mid': mobility_id}))


@app.on_event("startup")
def startup():
    validate_database(engine)
    start_form_poller48()


@app.get('/health/live')
def health_live():
    return {'status': 'ok'}


@app.get('/health/ready')
def health_ready():
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1 FROM dari_hosting_migration LIMIT 1')).one()
        return {'status': 'ok'}
    except Exception:
        raise HTTPException(503, 'Banco indisponível ou ainda não migrado.')


def _sidebar_pending_application_counts(request: Request) -> dict[str, int]:
    """Pendências de cada fluxo, para os contadores discretos do menu lateral.

    O resultado é consultado uma vez por requisição e não modifica o banco.
    Mantém a mesma regra da triagem: somente rejeitadas/aprovadas ficam fora
    das pendentes (inclusive para registros antigos com status nulo).
    """
    cached = getattr(request.state, "dari_pending_application_counts", None)
    if cached is not None:
        return cached
    counts = {"incoming": 0, "outgoing": 0}
    with SessionLocal() as sidebar_db:
        statement = (
            select(Application.direction, func.count(Application.id))
            .where(or_(Application.status.is_(None),
                       Application.status.notin_(("approved", "rejected"))))
            .group_by(Application.direction)
        )
        for direction, total in sidebar_db.execute(statement):
            if direction in counts:
                counts[direction] = int(total)
    request.state.dari_pending_application_counts = counts
    return counts


def common_context(request: Request, active: str):
    current_user = getattr(request.state, "user", None)
    return {
        "request": request,
        "active": active,
        "demo": os.getenv("DARI_NO_DEMO_SEED") != "1",
        "current_user": current_user,
        "permissions": _permissions(current_user),
        "pending_application_counts": _sidebar_pending_application_counts(request),
    }


def render(request: Request, name: str, context: dict):
    """Forma compatível com a assinatura atual do Starlette (1.x)."""
    if name in ('edit_mobility.html','new_mobility.html','edit_agreement.html','new_agreement.html'):
        from .catalog_live_v53_28 import choices
        with SessionLocal() as catalog_db:
            context.update(mobility_type_options=choices(catalog_db,'mobilities','mobility_type',('Intercâmbio Simples','Duplo Diploma','Estágio','Projeto Final')),
                agreement_type_options=choices(catalog_db,'agreements','agreement_type',('Acordo de Cooperação','Acordo de Duplo Diploma','Memorando de Entendimento','Acordo Erasmus','Acordo de Co-Tutela','Termo Aditivo')),
                progress_options=choices(catalog_db,'agreements','progress',('Em negociação','Em elaboração (SIG+)','Em tramitação (SEI)','Aguardando assinatura','Assinado')),
                ufrj_stage_options=choices(catalog_db,'agreements','ufrj_stage',('DARI','Parceira (IES)','SGRI','SAG','CCCT','CSCE','Superintendente','Assinado')))
    return templates.TemplateResponse(request=request, name=name, context=sort_context(context))


@app.get("/configuracoes", response_class=HTMLResponse)
def settings_page(request: Request, sec: str = "usuarios", status: str = "", erro: str = "", db: Session = Depends(get_db)):
    _require_admin(request)
    users = db.scalars(select(UserAccount).order_by(UserAccount.is_admin.desc(), func.lower(UserAccount.username))).all()
    messages = {
        "salvo": "Usuário e permissões atualizados.",
        "excluido": "Usuário excluído.",
        "calendario_salvo": "Calendário salvo. Previsões e filtros atualizados.",
        "calendario_atualizado": "Calendário atualizado do servidor central para o cache local.",
    }
    errors = {
        "usuario_vazio": "O nome de usuário não pode ficar vazio.",
        "usuario_existente": "Já existe outro usuário com esse login.",
        "admin": "A conta do administrador não pode ser excluída.",
        "calendario_invalido": "Confira o semestre e as datas informadas.",
        "calendario_offline": "Não foi possível acessar o servidor central. Nenhuma data foi alterada.",
        "calendario_servidor": "Falha no servidor central. Confira o estado do calendário antes de tentar novamente.",
        "calendario_cache": "O servidor central salvou o período, mas o cache local não pôde ser atualizado. Tente Atualizar cache deste computador.",
    }
    integration_sources = db.scalars(select(IntegrationSource).order_by(IntegrationSource.name)).all()
    agreement_total = db.scalar(select(func.count(Agreement.id)).where(_agreement_visible46())) or 0
    # Total historico homologado: nao contar importacoes Incoming provisorias.
    student_total = db.scalar(select(func.count(Student.id)).where(
        Student.id.not_in(select(Mobility.student_id).where(
            Mobility.status == _INCOMING_RECENT_PENDING)))) or 0
    mobility_total = db.scalar(select(func.count(Mobility.id)).where(
        Mobility.status != _INCOMING_RECENT_PENDING)) or 0
    db_backend = "SQLite local" if str(engine.url).startswith("sqlite") else engine.url.get_backend_name()
    academic_terms = read_cache(db)
    academic_current = next((r for r in academic_terms if r["is_current"]), None)
    context = common_context(request, "configuracoes")
    context.update({
        "settings_section": (("usuarios" if (sec or "usuarios") == "geral" else (sec or "usuarios")) if (sec or "usuarios") in {"usuarios","calendario","desenvolvimento","geral"} else "usuarios"),
        "users": users,
        "settings_message": messages.get(status),
        "settings_error": errors.get(erro),
        "system_build": SYSTEM_BUILD,
        "system_architecture_status": SYSTEM_ARCHITECTURE_STATUS,
        "db_backend": db_backend,
        "student_total": student_total,
        "mobility_total": mobility_total,
        "agreement_total": agreement_total,
        "integration_sources": integration_sources,
        "academic_terms": academic_terms,
        "academic_current": academic_current,
    })
    if context["settings_section"] == "desenvolvimento":
        from .database_reference_v53_31 import database_reference
        context["database_reference"] = database_reference(db)
    return render(request, "settings.html", context)



@app.post("/configuracoes/calendario/salvar")
def settings_calendar_save(
    request: Request,
    semester: str = Form(...), start_date: str = Form(...),
    end_date: str = Form(...), is_current: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(request)
    try:
        sem, ini, fim = validate_term(semester, start_date, end_date)
    except ValueError:
        return RedirectResponse("/configuracoes?sec=calendario&erro=calendario_invalido#calendario", status_code=303)
    save_cache(db, dict(semester=sem, start_date=ini, end_date=fim,
        is_current=(is_current == '1'), updated_at=datetime.utcnow().isoformat()))
    return RedirectResponse("/configuracoes?sec=calendario&status=calendario_salvo#calendario", status_code=303)


@app.post("/configuracoes/calendario/atualizar-cache")
def settings_calendar_pull(request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    return RedirectResponse('/configuracoes?sec=calendario', status_code=303)


@app.post("/configuracoes/usuarios/{user_id}/salvar")
def settings_user_save(
    user_id: int, request: Request,
    username: str = Form(...), new_password: str = Form(""),
    view_dashboard: str = Form(""), view_incoming: str = Form(""), edit_incoming: str = Form(""),
    view_outgoing: str = Form(""), edit_outgoing: str = Form(""),
    view_acordos: str = Form(""), edit_acordos: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_admin(request)
    target = db.get(UserAccount, user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado.")
    normalized = username.strip()
    if not normalized:
        return RedirectResponse("/configuracoes?sec=usuarios&erro=usuario_vazio#usuarios", status_code=303)
    duplicate = db.scalar(
        select(UserAccount).where(func.lower(UserAccount.username) == normalized.lower(), UserAccount.id != target.id).limit(1)
    )
    if duplicate:
        return RedirectResponse("/configuracoes?sec=usuarios&erro=usuario_existente#usuarios", status_code=303)

    old_username = target.username
    target.username = normalized
    if new_password and not password_ok(new_password):
        raise HTTPException(400, "Use uma senha exclusiva com pelo menos 12 caracteres.")
    if new_password:
        target.password_hash = _hash_password(new_password)

    if target.is_admin:
        target.can_view_dashboard = True
        target.can_view_incoming = True
        target.can_edit_incoming = True
        target.can_view_outgoing = True
        target.can_edit_outgoing = True
        target.can_view_acordos = True
        target.can_edit_acordos = True
    else:
        target.can_view_dashboard = bool(view_dashboard)
        target.can_view_incoming = bool(view_incoming)
        target.can_edit_incoming = bool(edit_incoming) and target.can_view_incoming
        target.can_view_outgoing = bool(view_outgoing)
        target.can_edit_outgoing = bool(edit_outgoing) and target.can_view_outgoing
        target.can_view_acordos = bool(view_acordos)
        target.can_edit_acordos = bool(edit_acordos) and target.can_view_acordos

    db.commit()
    response = RedirectResponse("/configuracoes?sec=usuarios&status=salvo#usuarios", status_code=303)
    current = getattr(request.state, "user", None)
    if current and current.get("id") == target.id and old_username != target.username:
        response.set_cookie(
            key=AUTH_COOKIE, value=_session_token(target.username),
            httponly=True, secure=settings.production, samesite="lax", max_age=60 * 60 * 8,
        )
    return response


@app.post("/configuracoes/usuarios/{user_id}/excluir")
def settings_user_delete(user_id: int, request: Request, db: Session = Depends(get_db)):
    _require_admin(request)
    target = db.get(UserAccount, user_id)
    if not target:
        raise HTTPException(404, "Usuário não encontrado.")
    if target.is_admin:
        return RedirectResponse("/configuracoes?sec=usuarios&erro=admin#usuarios", status_code=303)
    db.delete(target)
    db.commit()
    return RedirectResponse("/configuracoes?sec=usuarios&status=excluido#usuarios", status_code=303)


DOCUMENT_AREAS = {
    "incoming": {
        "title": "Incoming",
        "description": "Documentos usados no fluxo de estudantes estrangeiros.",
        "initial_folders": [
            "Formulários",
            "Modelos e declarações",
            "Materiais de orientação",
            "Processos - estudantes",
        ],
    },
    "outgoing": {
        "title": "Outgoing",
        "description": "Documentos usados no fluxo de estudantes brasileiros em mobilidade.",
        "initial_folders": [
            "Editais e formulários",
            "Modelos e declarações",
            "Materiais de orientação",
            "Processos - estudantes",
        ],
    },
    "acordos": {
        "title": "Acordos",
        "description": "Documentos relacionados à negociação, tramitação e vigência dos acordos institucionais.",
        # Estrutura local inspirada na organização atual do Drive da área de Acordos.
        # Estes diretórios são cópias/áreas próprias do sistema: o Drive nunca é alterado.
        "initial_folders": [
            "Acordos em Andamento",
            "Acordos Assinados",
            "00-Modelos",
            "Instruções e links importantes",
        ],
    },
}

DOCUMENT_STORAGE_DIR = Path(
    os.getenv(
        "DARI_DOCUMENTS_DIR",
        str(BASE_DIR.parent / "documentos"),
    )
)


def _clean_fs_name(value: str) -> str:
    """Nome seguro para Windows/Linux sem retirar acentos."""
    name = (value or "").strip().replace("\x00", "")
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    name = name.rstrip(" .")
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="Nome inválido.")
    return name


def _document_area_root(area: str) -> Path:
    if area not in DOCUMENT_AREAS:
        raise HTTPException(status_code=404, detail="Área documental inválida.")
    root = (DOCUMENT_STORAGE_DIR / area).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_document_path(area: str, relative_path: str = "") -> Path:
    root = _document_area_root(area)
    relative_path = (relative_path or "").strip().replace("\\", "/").lstrip("/")
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Caminho inválido.")
    return candidate


def _ensure_document_roots():
    """Cria a estrutura inicial uma única vez.

    O marcador impede que uma pasta renomeada/excluída pelo usuário reapareça
    em todo startup.
    """
    DOCUMENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    for area, cfg in DOCUMENT_AREAS.items():
        root = _document_area_root(area)
        marker = root / ".dari_initialized"
        if marker.exists():
            continue
        for folder_name in cfg.get("initial_folders", []):
            (root / _clean_fs_name(folder_name)).mkdir(parents=True, exist_ok=True)
        marker.write_text(
            "Estrutura documental local inicializada. Arquivos do Google Drive nunca são alterados.\n",
            encoding="utf-8",
        )


def _relative_document_path(area: str, path: Path) -> str:
    return path.resolve().relative_to(_document_area_root(area)).as_posix()


def _documents_redirect(area: str, current_path: str = "", status: str = "", erro: str = ""):
    params = []
    if current_path:
        params.append("pasta=" + quote(current_path, safe=""))
    if status:
        params.append("status=" + quote(status, safe=""))
    if erro:
        params.append("erro=" + quote(erro, safe=""))
    suffix = ("?" + "&".join(params)) if params else ""
    return RedirectResponse(f"/{area}/documentos{suffix}", status_code=303)



def parse_date(value: str | None):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()

@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    start_semester: str = "", end_semester: str | None = None, direction: str = "all",
    course_id: str = "", country_id: str = "", university_id: str = "",
    mobility_type: str = "", detail_page: int = 1, agreement_page: int = 1,
    process_active: str = "", db: Session = Depends(get_db),
):
    if not _can_view(request, "dashboard"):
        for area, url in (("incoming", "/incoming"), ("outgoing", "/outgoing"), ("acordos", "/acordos")):
            if _can_view(request, area):
                return RedirectResponse(url, status_code=303)
        _require_view(request, "dashboard")
    options = get_filter_options(db)
    if not start_semester and options["semesters"]:
        start_semester = options["semesters"][0].label
    # Na primeira abertura mantemos o intervalo completo na visão geral.
    # Quando o formulário envia end_semester vazio, vazio significa "somente o período inicial".
    if end_semester is None and options["semesters"]:
        end_semester = options["semesters"][-1].label
    filters = {
        "process_active": process_active,
        "start_semester": start_semester, "end_semester": end_semester or "",
        "direction": direction, "course_id": course_id,
        "country_id": country_id, "university_id": university_id,
        "mobility_type": mobility_type,
    }
    data = dashboard_data(db, filters)
    mobility_rows = mobility_detail_rows(data["mobilities"])
    agreement_rows = agreement_detail_rows(data["agreements_rows"])
    page_size = 50
    def page_slice(rows, number):
        total = max(1, (len(rows) + page_size - 1) // page_size)
        current = min(max(1, number), total)
        return rows[(current - 1) * page_size:current * page_size], current, total
    shown_mobilities, mobility_page_current, mobility_pages = page_slice(mobility_rows, detail_page)
    shown_agreements, agreement_page_current, agreement_pages = page_slice(agreement_rows, agreement_page)
    semchart = data["charts"]["semester"]
    semester_rows = []
    semester_lookup = {s.label: s.id for s in data["options"]["semesters"]}
    for i, label in enumerate(semchart["labels"]):
        counts = {ds["label"]: ds["data"][i] for ds in semchart["datasets"]}
        other = sum(1 for m in data["mobilities"] if m.start_semester_id == semester_lookup[label]
                    and m.direction not in {"incoming", "outgoing"})
        inc, out = counts.get("Incoming", 0), counts.get("Outgoing", 0)
        semester_rows.append({"semester": label, "incoming": inc,
            "outgoing": out, "other": other, "total": inc + out + other})
    ctx = common_context(request, "dashboard") | data | {
        "filters": filters,
        "filter_query": filter_query_string(filters),
        "mobility_columns": MOBILITY_DETAIL_COLUMNS,
        "agreement_columns": AGREEMENT_DETAIL_COLUMNS,
        "semester_rows": semester_rows,
        "mobility_rows": shown_mobilities,
        "mobility_row_total": len(mobility_rows),
        "mobility_page": mobility_page_current,
        "mobility_pages": mobility_pages,
        "agreement_rows": shown_agreements,
        "agreement_row_total": len(agreement_rows),
        "agreement_page": agreement_page_current,
        "agreement_pages": agreement_pages,
    }
    return render(request, "dashboard.html", ctx)


@app.get("/relatorios/{slug}", response_class=HTMLResponse)
def report_view(
    slug: str, request: Request,
    start_semester: str = "", end_semester: str | None = None, direction: str = "all",
    course_id: str = "", country_id: str = "", university_id: str = "", mobility_type: str = "", university_chart_limit: str = "10", category_chart_limit: str = "10",
    detail_page: int = 1, process_active: str = "", db: Session = Depends(get_db),
):
    _require_view(request, "dashboard")
    filters = {
        "process_active": process_active,
        "start_semester": start_semester, "end_semester": end_semester or "",
        "direction": direction, "course_id": course_id,
        "country_id": country_id, "university_id": university_id, "mobility_type": mobility_type,
        "university_chart_limit": university_chart_limit, "category_chart_limit": category_chart_limit,
    }
    try:
        report = build_report(db, slug, filters)
    except KeyError:
        return RedirectResponse("/", status_code=303)
    detailed = None
    if slug in {"mobilidade-semestre", "acordos-andamento"}:
        extra_slug = "mobilidades" if slug == "mobilidade-semestre" else "acordos"
        extra = build_report(db, extra_slug, filters)
        page_size = 50
        total_pages = max(1, (len(extra["rows"]) + page_size - 1) // page_size)
        page = min(max(1, detail_page), total_pages)
        detailed = {"title": "Estudantes do período" if slug == "mobilidade-semestre" else "Acordos do recorte",
                    "columns": extra["columns"], "rows": extra["rows"][(page-1)*page_size:page*page_size],
                    "total": len(extra["rows"]), "page": page, "pages": total_pages,
                    "export_slug": extra_slug}
    return render(request, "report.html", common_context(request, "dashboard") | {
        "report": report, "filters": filters, "filter_query": filter_query_string(filters), "detailed": detailed
    })


@app.get("/relatorios/{slug}/pdf")
def report_pdf(
    slug: str, start_semester: str = "", end_semester: str | None = None, direction: str = "all",
    course_id: str = "", country_id: str = "", university_id: str = "", mobility_type: str = "", university_chart_limit: str = "10", category_chart_limit: str = "10",
    process_active: str = "", db: Session = Depends(get_db),
):
    filters = {
        "process_active": process_active,
        "start_semester": start_semester, "end_semester": end_semester or "",
        "direction": direction, "course_id": course_id,
        "country_id": country_id, "university_id": university_id, "mobility_type": mobility_type,
        "university_chart_limit": university_chart_limit, "category_chart_limit": category_chart_limit,
    }
    try:
        report = build_report(db, slug, filters)
    except KeyError:
        return RedirectResponse("/", status_code=303)
    content = report_to_pdf(report)
    filename = f"DARI_{slug}.pdf"
    return StreamingResponse(iter([content]), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'
    })


def _search_like(column, term: str):
    return column.ilike(f"%{term}%")


def _search_cast(column, term: str):
    return cast(column, String).ilike(f"%{term}%")


def _parsed_search_date(term: str):
    raw = term.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


def _apply_mobility_search(stmt, direction: str, q: str, db: Session | None = None):
    """Busca textual ampla pelos campos exibidos nas tabelas de mobilidade.

    O filtro é mantido no servidor para que a tela e as exportações Excel
    retornem exatamente o mesmo conjunto de registros.
    """
    term = (q or "").strip()
    if not term:
        return stmt

    stmt = (
        stmt
        .join(Mobility.student)
        .outerjoin(Mobility.course)
        .outerjoin(Mobility.program)
        .outerjoin(Mobility.coordinator)
        .outerjoin(Mobility.start_semester)
    )

    common = [
        _search_like(Student.full_name, term),
        _search_like(Student.dre, term),
        _search_like(Student.email, term),
        _search_like(Course.name, term),
        _search_like(Program.name, term),
        _search_like(Program.code, term),
        _search_like(Coordinator.name, term),
        _search_like(Coordinator.email, term),
        _search_like(Semester.label, term),
        _search_cast(Mobility.duration_semesters, term),
        _search_like(Mobility.mobility_type, term),
        _search_like(Mobility.status, term),
    ]

    if direction == "incoming":
        stmt = (
            stmt
            .outerjoin(Mobility.home_university)
            .outerjoin(University.country)
            .outerjoin(Mobility.incoming_detail)
        )
        conditions = common + [
            _search_like(Student.passport, term),
            _search_like(University.name, term),
            _search_like(University.acronym, term),
            _search_like(Country.name, term),
            _search_like(IncomingDetail.polimail, term),
            _search_like(IncomingDetail.responsible_name, term),
            _search_like(IncomingDetail.responsible_email, term),
            _search_like(IncomingDetail.dari_form, term),
            _search_like(IncomingDetail.study_plan, term),
        ]
        if db is not None:
            # v52: busca por nome atual engloba identificadores históricos homologados,
            # inclusive mobilidades cujo campo de universidade segue no pré-cadastro.
            ids = matching_universities(db, term)
            if ids:
                conditions.append(Mobility.home_university_id.in_(sorted(ids)))
                labels = sorted(matching_source_labels(db, ids))
                if labels and len(labels) <= 500:
                    conditions.append(Mobility.id.in_(select(column('mobility_id')).select_from(
                        table('incoming_recent_promotions')).where(
                        column('source_university').in_(labels))))
    else:
        stmt = (
            stmt
            .outerjoin(Mobility.destination_university)
            .outerjoin(University.country)
            .outerjoin(Mobility.outgoing_detail)
        )
        conditions = common + [
            _search_like(University.name, term),
            _search_like(University.acronym, term),
            _search_like(Country.name, term),
            _search_like(Mobility.scholarship, term),
            _search_like(Mobility.extension, term),
            _search_like(OutgoingDetail.registration_semester, term),
            _search_cast(OutgoingDetail.expected_return, term),
        ]
        parsed = _parsed_search_date(term)
        if parsed is not None:
            conditions.append(OutgoingDetail.expected_return == parsed)

    return stmt.where(or_(*conditions))


def _apply_agreement_search(stmt, q: str):
    """Busca ampla pelos campos apresentados na tabela de Acordos."""
    term = (q or "").strip()
    if not term:
        return stmt

    stmt = stmt.join(Agreement.university).outerjoin(University.country)
    conditions = [
        _search_like(University.name, term),
        _search_like(University.acronym, term),
        _search_like(Country.name, term),
        _search_like(Agreement.agreement_type, term),
        _search_like(Agreement.sig_number, term),
        _search_like(Agreement.sei_number, term),
        _search_like(Agreement.progress, term),
        _search_like(Agreement.ufrj_stage, term),
        _search_cast(Agreement.signed_date, term),
        _search_cast(Agreement.validity_years, term),
        _search_cast(Agreement.expiry_date, term),
        _search_like(Agreement.status, term),
        _search_like(Agreement.ufrj_coordinator, term),
        _search_like(Agreement.notes, term),
        Agreement.actions.any(or_(
            _search_like(AgreementAction.last_action, term),
            _search_like(AgreementAction.next_step, term),
            _search_like(AgreementAction.deadline_text, term),
            _search_like(AgreementAction.notes, term),
        )),
    ]
    parsed = _parsed_search_date(term)
    if parsed is not None:
        conditions.extend([Agreement.signed_date == parsed, Agreement.expiry_date == parsed])
    return stmt.where(or_(*conditions))


def select_export_columns(report: dict, indices: str) -> dict:
    """Seleciona somente colunas visíveis; sem parâmetro exporta todas.

    A seleção é feita no servidor; índices não autorizados produzem 422 em vez
    de adicionar campos, duplicá-los ou fazer uma exportação enganosa.
    """
    if not indices:
        return report
    pieces = indices.split(",")
    if not pieces or any(not item.isdecimal() for item in pieces):
        raise HTTPException(422, "Seleção de colunas inválida.")
    selected = [int(item) for item in pieces]
    if len(set(selected)) != len(selected) or any(idx < 0 or idx >= len(report["columns"]) for idx in selected):
        raise HTTPException(422, "Seleção de colunas inválida.")
    # A ordem da exportação segue a ordem original da tabela.
    chosen = sorted(selected)
    return report | {
        "columns": [report["columns"][i] for i in chosen],
        "rows": [[row[i] for i in chosen] for row in report["rows"]],
    }


@app.get("/relatorios/{slug}/xlsx")
def report_xlsx(
    slug: str, columns: str = "", start_semester: str = "", end_semester: str | None = None, direction: str = "all",
    course_id: str = "", country_id: str = "", university_id: str = "", mobility_type: str = "", university_chart_limit: str = "5",
    process_active: str = "", db: Session = Depends(get_db),
):
    filters = {
        "process_active": process_active,
        "start_semester": start_semester, "end_semester": end_semester or "",
        "direction": direction, "course_id": course_id,
        "country_id": country_id, "university_id": university_id, "mobility_type": mobility_type,
        "university_chart_limit": university_chart_limit,
    }
    try:
        report = build_report(db, slug, filters)
    except KeyError:
        return RedirectResponse("/", status_code=303)
    content = report_to_xlsx(select_export_columns(report, columns))
    filename = f"DARI_{slug}.xlsx"
    return StreamingResponse(iter([content]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'
    })


def operational_export(db: Session, area: str, q: str, processo: str = "todos", periodo: str = "todos", situacao: str = "todos") -> dict:
    """Mesmos campos e filtros das telas operacionais; a coluna Ações nunca é exportada."""
    if area in {"incoming", "outgoing"}:
        stmt = select(Mobility).options(*mobility_query_options()).where(Mobility.direction == area, _mobility_not_deleted())
        if area=='incoming':
            stmt=stmt.where(Mobility.status != _INCOMING_RECENT_PENDING)
        stmt = _apply_mobility_search(stmt, area, q, db)
        mobilities = db.scalars(stmt.order_by(Mobility.id.desc())).unique().all()
        mobilities, export_context = listing29(db, mobilities, periodo, 'concluidos' if processo=='concluidos' else 'todos', 'todos')
        if area == "incoming":
            columns = ["Nome", "DRE", "Passaporte", "E-mail", "Instituição de origem", "País de origem", "Curso", "Projeto", "Coordenador", "Semestre", "Duração", "Mobilidade", "Status", "Polimail", "Responsável", "E-mail do responsável", "Form. DARI", "Plano de estudos"]
            rows = []
            for m in mobilities:
                d = m.incoming_detail
                rows.append([
                    m.student.full_name, m.student.dre or "-", m.student.passport or "-", m.student.email or "-",
                    m.home_university.name if m.home_university else "-",
                    m.home_university.country.name if m.home_university and m.home_university.country else "-",
                    m.course.name if m.course else "-", m.program.name if m.program else "-",
                    m.coordinator.name if m.coordinator else "-", m.start_semester.label if m.start_semester else "-",
                    m.duration_semesters if m.duration_semesters is not None else "-", m.mobility_type or "-", m.status or "-",
                    d.polimail if d and d.polimail else "-", d.responsible_name if d and d.responsible_name else "-",
                    d.responsible_email if d and d.responsible_email else "-", d.dari_form if d and d.dari_form else "-",
                    d.study_plan if d and d.study_plan else "-",
                ])
        else:
            columns = ["Nome", "DRE", "E-mail", "Curso", "Programa/Projeto", "Coordenador", "Destino", "País de destino", "Semestre", "Duração", "Mobilidade", "Bolsa", "Prorrogação", "Previsão de retorno", "Status"]
            rows = []
            for m in mobilities:
                d = m.outgoing_detail
                rows.append([
                    m.student.full_name, m.student.dre or "-", m.student.email or "-",
                    m.course.name if m.course else "-", m.program.name if m.program else "-",
                    m.coordinator.name if m.coordinator else "-", m.destination_university.name if m.destination_university else "-",
                    m.destination_university.country.name if m.destination_university and m.destination_university.country else "-",
                    m.start_semester.label if m.start_semester else "-",
                    m.duration_semesters if m.duration_semesters is not None else "-", m.mobility_type or "-",
                    m.scholarship or "-", m.extension or "-", getattr(m,"_return_label40",None) or "-",
                    m.status or "-",
                ])
        status_index=columns.index('Status')
        columns.insert(1,columns.pop(status_index))
        for row,m in zip(rows,mobilities):
            row.pop(status_index)
            row.insert(1,' • '.join(label for _,label in export_context['status_labels'].get(m.id,[])) or m.status or '-')
            row[columns.index('Semestre')]=export_context['period_labels'].get(m.id) or '-'
    elif area == "acordos":
        stmt = select(Agreement).options(joinedload(Agreement.university).joinedload(University.country), selectinload(Agreement.actions)).where(_agreement_visible46())
        stmt = _apply_agreement_search(stmt, q)
        agreements = db.scalars(stmt.order_by(Agreement.id.desc())).unique().all()
        from .agreement_archive_v53_19 import refresh_archive
        archived_sheet_ids = refresh_archive(db)
        agreements = [a for a in agreements if a.id not in archived_sheet_ids]
        columns = ["Universidade", "Sigla", "País", "Tipo do acordo", "Nº SIG+", "Nº SEI", "Andamento", "Etapa atual UFRJ", "Assinatura", "Vigência (anos)", "Validade", "Status", "Coordenador UFRJ", "Próximo passo", "Prazo", "Observações"]
        rows = []
        for a in agreements:
            last = a.actions[-1] if a.actions else None
            rows.append([
                a.university.name, a.university.acronym or "-",
                a.university.country.name if a.university.country else "-",
                a.agreement_type, a.sig_number or "-", a.sei_number or "-", a.progress or "-",
                a.ufrj_stage or "-", a.signed_date.strftime("%d/%m/%Y") if a.signed_date else "-",
                a.validity_years if a.validity_years is not None else "-", a.expiry_date.strftime("%d/%m/%Y") if a.expiry_date else "-",
                a.status or "-", a.ufrj_coordinator or "-", last.next_step if last and last.next_step else "-",
                last.deadline_text if last and last.deadline_text else "-", a.notes or "-",
            ])
    else:
        raise HTTPException(404, "Área inexistente.")
    return {"title": f"DARI — {area.title()}", "description": "Exportação da tabela operacional do sistema DARI.", "filters": [f"Pesquisa: {q}" if q else "Todas as entradas exibidas"], "columns": columns, "rows": rows}


@app.get("/tabelas/{area}/xlsx")
def operational_xlsx(area: str, q: str = "", columns: str = "", processo: str = "todos", periodo: str = "todos", situacao: str = "todos", db: Session = Depends(get_db)):
    report = select_export_columns(operational_export(db, area, q, processo, periodo, situacao), columns)
    content = report_to_xlsx(report)
    return StreamingResponse(iter([content]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={
        "Content-Disposition": f'attachment; filename="DARI_{area}_tabela.xlsx"'
    })


# DARI_INCOMING_EXPORT_V52_20_START
@app.get("/tabelas/incoming/visao.xlsx")
def incoming_view_xlsx(
    request: Request, q: str = "", periodo: str = "todos", origem: str = "todos", processo: str = "todos", situacao: str = "todos",
    columns: str = "", db: Session = Depends(get_db),
):
    """Exporta somente leitura o recorte atual da tabela Incoming."""
    _require_view(request, "incoming")
    from .incoming_table_export import build_incoming_table_report
    # Reutiliza a propria consulta da pagina para manter filtros, conciliacao,
    # ordem e registros provisorios identicos ao que aparece na tela.
    response = incoming(request, q=q, periodo=periodo, origem=origem, processo=processo, situacao=situacao, db=db)
    context = response.context
    report = build_incoming_table_report(
        context, q=q, periodo=context["periodo"], origem=context["origem"]
    )
    report = select_export_columns(report, columns)
    content = report_to_xlsx(report)
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="DARI_Incoming_tabela.xlsx"'},
    )
# DARI_INCOMING_EXPORT_V52_20_END


APPLICATION_FILES_DIR = Path(os.getenv("DARI_APPLICATION_FILES_DIR", BASE_DIR.parent / "application_files")).resolve()

INCOMING_APPLICATION_FIELDS = [
    ("academic_semester", "Ano Letivo/Semestre na UFRJ"),
    ("application_type", "Informações da Candidatura"),
    ("field_of_study", "Área de Estudos"),
    ("stay_duration_semesters", "Duração da Estadia (semestres)"),
    ("exchange_start", "Data de Início"),
    ("exchange_end", "Data do Fim"),
    ("has_ufrj_advisor", "Você já possui um professor responsável/orientador na UFRJ?"),
    ("ufrj_advisor_name", "Se sim, qual é o nome do professor?"),
    ("laboratory_interest", "Em qual laboratório você tem interesse?"),
    ("first_name", "Primeiro Nome"),
    ("surnames", "Último Nome/Sobrenome(s)"),
    ("full_name", "Nome completo"),
    ("gender", "Gênero"),
    ("birth_date", "Data de Nascimento"),
    ("country_origin", "País de Origem"),
    ("passport", "Número do Passaporte"),
    ("mailing_address", "Endereço de Correspondência"),
    ("zip_code", "CEP / Código Postal"),
    ("email", "E-mail"),
    ("phone", "Telefone"),
    ("mobile", "Celular"),
    ("home_university", "Selecione sua Universidade de Origem"),
    ("home_university_address", "Endereço Completo da Universidade de Origem"),
    ("course_coordinator", "Coordenador(a) do Curso"),
    ("course_coordinator_email", "Endereço de E-mail do(a) Coordenador(a) de Curso"),
    ("mobility_professor", "Professor(a) Responsável pela Mobilidade"),
    ("mobility_professor_email", "Endereço de E-mail do(a) Professor(a) Responsável pela Mobilidade"),
    ("current_degree", "Curso em Andamento em sua Instituição Atual"),
    ("degree_duration_years", "Duração em anos"),
    ("degree_start", "Data de Início da graduação"),
    ("current_semester", "Semestre Atual"),
    ("degree_completion", "Data de Conclusão (real ou prevista)"),
]

OUTGOING_APPLICATION_FIELDS = [
    ("academic_semester", "Ano/Semestre"),
    ("application_type", "Tipo de candidatura"),
    ("full_name", "Nome completo"),
    ("dre", "DRE"),
    ("email", "E-mail"),
    ("field_of_study", "Curso/Área de estudos"),
    ("destination_university", "Instituição de destino"),
    ("destination_country", "País de destino"),
]


def _application_payload(record: Application) -> dict:
    import json
    try:
        value = json.loads(record.raw_payload_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _application_full_name(record: Application) -> str:
    if record.full_name and record.full_name.strip():
        return record.full_name.strip()
    parts = [record.first_name or "", record.surnames or ""]
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()


def _application_value(record: Application, payload: dict, key: str):
    core = {
        "academic_semester": record.academic_semester,
        "application_type": record.application_type,
        "first_name": record.first_name,
        "surnames": record.surnames,
        "full_name": _application_full_name(record),
        "email": record.email,
        "dre": record.dre,
        "passport": record.passport,
        "country_origin": record.country_name,
        "field_of_study": record.course_name,
        "home_university": canonical_label(record.institution_name),
        "destination_university": canonical_label(record.institution_name),
    }
    value = core.get(key)
    if value not in (None, ""):
        return value
    return payload.get(key)


def _application_display_value(value):
    translations = {
        "Double Degree": "Duplo Diploma",
        "Non-Degree Studies": "Estudos sem Obtenção de Grau",
        "Research Mobility/Internship": "Mobilidade de Pesquisa/Estágio",
        "Male": "Masculino",
        "Female": "Feminino",
        "Other": "Outro",
        "Yes": "Sim",
        "No": "Não",
    }
    if isinstance(value, str):
        return translations.get(value.strip(), value)
    return value


def _application_response_pairs(record: Application) -> list[tuple[str, str]]:
    payload = _application_payload(record)
    definitions = INCOMING_APPLICATION_FIELDS if record.direction == "incoming" else OUTGOING_APPLICATION_FIELDS
    pairs = []
    used = set()
    for key, label in definitions:
        value = _application_value(record, payload, key)
        used.add(key)
        if value not in (None, "", []):
            if isinstance(value, (list, tuple)):
                value = ", ".join(str(item) for item in value)
            elif isinstance(value, bool):
                value = "Sim" if value else "Não"
            pairs.append((label, str(_application_display_value(value))))
    # Preservar todas as perguntas originais, inclusive formulários novos e campos alterados.
    # A importação não altera a estrutura do banco a cada mudança de pergunta.
    for key, value in payload.items():
        if key in used or key.startswith('__') or value in (None, "", []):
            continue
        label = str(key).strip()
        if isinstance(value, (list, tuple)):
            value = ', '.join(str(v) for v in value)
        pairs.append((label, str(_application_display_value(value))))
    return pairs


def _application_context(db: Session, direction: str) -> dict:
    records = db.scalars(
        select(Application)
        .where(Application.direction == direction)
        .order_by(Application.submitted_at.desc(), Application.id.desc())
    ).all()
    pending = []
    rejected = []
    approved = []
    form_details48 = application_extras(db, [r.id for r in records])
    for record in records:
        item = {"record": record, "responses": _application_response_pairs(record),
                "form_response": form_details48.get(record.id)}
        if record.status == "rejected":
            rejected.append(item)
        elif record.status == "approved":
            approved.append(item)
        else:
            pending.append(item)
    pending_groups = {}
    for item in pending:
        source_info = item.get('form_response')
        source = source_info['source'] if source_info else None
        key = f'form-{source.id}' if source else 'outros'
        if key not in pending_groups:
            pending_groups[key] = {
                'name': source.name if source else 'Outras candidaturas',
                'stage': (source.stage if source else ''),
                'items': [],
            }
        pending_groups[key]['items'].append(item)
    return {
        "application_pending": pending,
        "application_pending_groups": list(pending_groups.values()),
        "application_rejected": rejected,
        "application_approved": approved,
        "application_approved_count": len(approved),
    }


def _lookup_course(db: Session, name: str | None):
    if not name:
        return None
    return db.scalar(select(Course).where(func.lower(Course.name) == name.strip().lower()).limit(1))


def _lookup_university(db: Session, name: str | None):
    if not name:
        return None
    # DARI_V53_20_UNIVERSITY_NAMES: preservar correspondências antigas na busca.
    found = db.scalar(select(University).where(func.lower(University.name) == name.strip().lower()).limit(1))
    if found:
        return found
    from .university_reference_v53_20 import resolve_alias_orm
    return resolve_alias_orm(db,name)


def _lookup_semester(db: Session, label: str | None):
    if not label:
        return None
    raw = label.strip()
    variants = {raw, raw.replace("/", "."), raw.replace(".", "/")}
    return db.scalar(select(Semester).where(Semester.label.in_(variants)).limit(1))


def _application_duration(payload: dict):
    value = payload.get("stay_duration_semesters") or payload.get("duration_semesters")
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _find_existing_student(db: Session, record: Application):
    if record.passport:
        student = db.scalar(select(Student).where(Student.passport == record.passport.strip()).limit(1))
        if student:
            return student
    if record.dre:
        student = db.scalar(select(Student).where(Student.dre == record.dre.strip()).limit(1))
        if student:
            return student
    if record.email:
        student = db.scalar(select(Student).where(func.lower(Student.email) == record.email.strip().lower()).limit(1))
        if student:
            return student
    return None



# DARI_V53_48_3_INCOMING_FORM_CORRECTIONS
# A fonte original e o JSON bruto da resposta não são alterados ao visualizar a ficha.
def _application_form_start48_3(payload: dict) -> str:
    from .form_bridge_v53_48 import norm
    for key, value in payload.items():
        if norm(key).startswith('start date format mm yyyy') and str(value or '').strip():
            return str(value).strip()
    return ''


def _application_semester_from_calendar48_3(db: Session, month_year: str) -> str:
    import calendar
    import re
    match = re.fullmatch(r'\s*(0?[1-9]|1[0-2])[/.-](20\d{2})\s*', month_year or '')
    if not match:
        return ''
    month, year = int(match.group(1)), int(match.group(2))
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    from sqlalchemy import inspect
    if not inspect(db.get_bind()).has_table('dari_academic_calendar_cache'):
        return ''
    rows = db.execute(text('SELECT semester, start_date, end_date FROM dari_academic_calendar_cache')).all()
    matches = []
    for semester, start, end in rows:
        try:
            a, z = date.fromisoformat(start), date.fromisoformat(end)
        except (ValueError, TypeError):
            continue
        overlap = (min(z, last) - max(a, first)).days + 1
        if overlap > 0:
            matches.append((overlap, str(semester)))
    if not matches:
        return ''
    matches.sort(reverse=True)
    # Se o mes cruza dois semestres com cobertura identica, nao adivinhar.
    if len(matches) > 1 and matches[0][0] == matches[1][0]:
        return ''
    return matches[0][1]


def _application_calendar_ranges48_3(db: Session):
    from sqlalchemy import inspect
    if not inspect(db.get_bind()).has_table('dari_academic_calendar_cache'):
        return []
    return [dict(x) for x in db.execute(text(
        'SELECT semester, start_date, end_date FROM dari_academic_calendar_cache ORDER BY semester DESC'
    )).mappings()]


def _application_country48_3(db: Session, original: str):
    from .form_bridge_v53_48 import norm
    countries = db.scalars(select(Country).order_by(Country.name)).all()
    normalized = norm(original)
    aliases = {
        'france':'franca', 'germany':'alemanha', 'spain':'espanha',
        'italy':'italia', 'portugal':'portugal', 'united kingdom':'reino unido',
        'united states':'estados unidos', 'netherlands':'holanda',
        'belgium':'belgica', 'switzerland':'suica', 'japan':'japao',
        'canada':'canada', 'australia':'australia', 'chile':'chile',
        'colombia':'colombia', 'argentina':'argentina',
    }
    normalized = aliases.get(normalized, normalized)
    matched = next((country.name for country in countries if norm(country.name) == normalized), '')
    return matched, countries


# DARI_V53_48_2_DRAFT_INCOMING_OUTGOING
# A ficha de triagem e uma pre-visualizacao editavel. GET e Voltar NAO persistem.
def _application_draft48_2(db: Session, record: Application):
    payload = _application_payload(record)
    # Os nomes importados do Forms sao as colunas originais da planilha;
    # correspondencias sao conservadoras, sem inventar dados ausentes.
    from .form_bridge_v53_48 import norm
    fields = {norm(k): str(v).strip() for k, v in payload.items() if v not in (None, '')}
    def value(*keys):
        for key in keys:
            found = fields.get(norm(key), '')
            if found:
                return found
        return ''
    country_raw = record.country_name or value('Country of Origin', 'country_origin')
    country, _countries = _application_country48_3(db, country_raw)
    raw_type = value('Application Information', 'Tipo de mobilidade', 'application_type')
    # Nunca usar o nome da etapa (Inscrição Incoming) como modalidade.
    stage_labels = {'inscrição incoming', 'inscricao incoming'}
    mobility_type = raw_type or (record.application_type if (record.application_type or '').casefold().strip() not in stage_labels else '')
    if record.direction == 'outgoing' and mobility_type and not raw_type:
        # Respostas importadas em versoes anteriores podem ter recebido o titulo
        # da fonte como tipo de mobilidade. O formulario nao define modalidade.
        matched_form = db.execute(select(FormSource).join(
            FormResponse, FormResponse.source_id == FormSource.id).where(
            FormResponse.application_id == record.id).limit(1)).scalar_one_or_none()
        if matched_form and mobility_type.strip() in {matched_form.stage.strip(), matched_form.name.strip()}:
            mobility_type = ''
    start_date = _application_form_start48_3(payload)
    calendar_semester = _application_semester_from_calendar48_3(db, start_date) if record.direction == 'incoming' else ''
    course = _lookup_course(db, record.course_name or value('field_of_study', 'Field of Studies -  This determines your Main Course '))
    uni = _lookup_university(db, record.institution_name or value('home_university', 'destination_university'))
    dur = _application_duration(payload)
    if dur is None:
        raw_dur=value('Lenght of Stay (semesters)', 'Length of Stay (semesters)', 'stay_duration_semesters')
        try: dur=int(raw_dur) if raw_dur else None
        except ValueError: dur=None
    return {
        'name': _application_full_name(record), 'email': record.email or '',
        'dre': record.dre or '', 'passport': record.passport or '',
        'country': country or '', 'country_original': country_raw or '',
        'start_date_form': start_date, 'calendar_semester': calendar_semester,
        'course_id': course.id if course else 0,
        'course_original': record.course_name or '',
        'university_id': uni.id if uni else 0,
        'university_original': record.institution_name or '',
        'program_id': 0, 'coordinator_id': 0,
        'semester_label': calendar_semester or record.academic_semester or '',
        'mobility_type': mobility_type,
        'duration_semesters': dur,
        'candidate_document': record.document_local_path or record.document_url or value('File Upload'),
        'polimail':'', 'intranet_access':'', 'identification':'', 'dari_form':'',
        'study_plan':'', 'coordination_notes':'',
        'responsible_name': value('If yes, what is their name?', 'ufrj_advisor_name'),
        'responsible_email': '',
        'registration_semester':'', 'notes':'', 'scholarship':'', 'extension':'',
    }


def _application_extras48_2(record: Application):
    from .form_bridge_v53_48 import norm
    payload = _application_payload(record)
    # Campos que ja aparecem nos cards Estudante, Mobilidade e Instituicao.
    mapped = {
        'nome','nome completo','full name','full_name','email','e mail','dre',
        'passport number','passport','country of origin','country_origin',
        'academic year semester at ufrj','academic_semester',
        'field of studies this determines your main course','field_of_study',
        'select your home university','home_university', 'destination_university',
        'application information','application_type', 'lenght of stay semesters',
        'length of stay semesters','stay_duration_semesters',
        'file upload', 'ufrj_advisor_name','if yes what is their name',
        'first given name','last surname s','first_name','surnames',
    }
    result=[]
    for label, val in payload.items():
        name=str(label).strip()
        if not name or name.startswith('__'):continue
        if norm(name) in mapped or norm(name).startswith('start date format mm yyyy'):continue
        if isinstance(val,(list,tuple)):val=', '.join(str(v) for v in val)
        result.append((name, str(val) if val is not None else ''))
    return result


def _application_fk48_2(db, model, raw):
    try: pk=int(str(raw or '0'))
    except ValueError:raise HTTPException(422,'Identificador de cadastro invalido.')
    if pk <= 0:return None
    if db.get(model,pk) is None:raise HTTPException(422,'Cadastro selecionado nao existe. Atualize a ficha.')
    return pk


def _application_period48_2(db, value):
    semester=(value or '').strip()
    if not semester:return None
    try:
        semester=valid_semester(semester)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    return ensure_semester29(db, semester, 0)


# DARI_V53_48_1_CANDIDATURAS_NAV: navegação e fichas separadas.
def _application_group_v53_48_1(items):
    groups = {}
    for item in items:
        detail = item.get('form_response')
        source = detail.get('source') if detail else None
        key = ('form', source.id) if source else ('outros', 0)
        if key not in groups:
            groups[key] = {'name': source.name if source else 'Outras candidaturas',
                           'stage': source.stage if source else '', 'source_id': source.id if source else 0, 'items': []}
        groups[key]['items'].append(item)
    return list(groups.values())


@app.get("/candidaturas/ficha/{application_id}", response_class=HTMLResponse)
def application_detail_v53_48_1(application_id: int, request: Request, novo: int = 0, db: Session = Depends(get_db)):
    record = db.get(Application, application_id)
    if not record or record.direction not in ('incoming', 'outgoing'):
        raise HTTPException(404, 'Candidatura não encontrada.')
    _require_view(request, record.direction)
    detail = application_extras(db, [record.id]).get(record.id)
    item = {'record': record, 'responses': _application_response_pairs(record), 'form_response': detail}
    ctx = mobility_form_context(request, db, record.direction)
    draft = _application_draft48_2(db, record)
    _selected_country, available_countries = _application_country48_3(db, draft.get('country_original', ''))
    raw_form_fields = []
    if detail:
        try:
            raw = json.loads(detail['response'].raw_fields_json or '{}')
            raw_form_fields = [(str(k), str(v)) for k, v in raw.items() if str(v or '').strip()]
        except (ValueError, TypeError):
            raw_form_fields = []
    ctx.update({'form_fields48': raw_form_fields, 'novo48': novo == 1,
                'application48': item, 'draft48': draft,
                'extra48': _application_extras48_2(record), 'countries48': available_countries,
                'calendar48': _application_calendar_ranges48_3(db),
                'mobility_options48': ['Duplo Diploma', 'Estágio', 'Intercâmbio Simples',
                    'Monografia de Pesquisa', 'Projeto Final'],
                'source_name48': detail['source'].name if detail else '',
                'direction': record.direction})
    if detail and record.status == 'pending' and novo == 1:
        return RedirectResponse(f"/novo/{record.direction}?form_response={detail['response'].id}", status_code=303)
    return render(request, 'application_detail_v53_48_1.html', ctx)


# DARI_V53_48_2_CANDIDATURAS_TABLE_EXPORT
@app.get('/candidaturas/{direction}/{state}/visao.xlsx')
def application_table_export48_2(direction:str,state:str,request:Request,columns:str='',source_id:int=-1,db:Session=Depends(get_db)):
    if direction not in ('incoming','outgoing') or state not in ('pendentes','rejeitadas'):
        raise HTTPException(404)
    _require_view(request,direction)
    listing=_application_context(db,direction)
    items=listing['application_pending'] if state=='pendentes' else listing['application_rejected']
    if source_id >= 0:
        items = [item for item in items if (item.get('form_response') and item['form_response']['source'].id == source_id) or (source_id == 0 and not item.get('form_response'))]
    headers=['Nome','Status','DRE','Passaporte','E-mail',
             'Instituicao de origem' if direction=='incoming' else 'Universidade de destino',
             'Curso','Semestre','Tipo de candidatura','Recebida em','Ações']
    data=[]
    for item in items:
        a=item['record'];detail=item.get('form_response')
        data.append([
            _application_full_name(a) or '-', 'Pendente' if a.status=='pending' else 'Rejeitada',
            a.dre or '-',a.passport or '-',a.email or '-',a.institution_name or '-',
            a.course_name or '-',a.academic_semester or '-',a.application_type or '-',
            a.submitted_at.strftime('%d/%m/%Y %H:%M') if a.submitted_at else '-',
            'Abrir',
        ])
    report={'title':'DARI - Candidaturas '+state+' - '+direction.title(),
            'description':'Respostas importadas, somente leitura na fonte Google.',
            'columns':headers,'rows':data,'filters':[]}
    content=report_to_xlsx(select_export_columns(report,columns))
    return StreamingResponse(iter([content]),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':f'attachment; filename="DARI_{direction}_candidaturas_{state}.xlsx"'})


@app.get("/candidaturas/{direction}/{state}", response_class=HTMLResponse)
def application_list_v53_48_1(direction: str, state: str, request: Request, db: Session = Depends(get_db)):
    if direction not in ('incoming', 'outgoing') or state not in ('pendentes', 'rejeitadas'):
        raise HTTPException(404, 'Página de candidaturas não encontrada.')
    _require_view(request, direction)
    context = _application_context(db, direction)
    records = context['application_pending'] if state == 'pendentes' else context['application_rejected']
    return render(request, 'applications_list_v53_48_1.html', common_context(request, direction) | {
        'direction48': direction,
        'state48': 'pending' if state == 'pendentes' else 'rejected',
        'items48': records,
        'groups48': _application_group_v53_48_1(records),
    })


@app.post("/candidaturas/{application_id}/aprovar")
def approve_application(
    application_id: int, request: Request, acao: str = Form(''),
    name: str = Form(''), email: str = Form(''), dre: str = Form(''), passport: str = Form(''),
    nationality: str = Form(''), start_date_form: str = Form(''),
    extra_key: list[str] = Form([]), extra_value: list[str] = Form([]), course_id: str = Form('0'),
    program_id: str = Form('0'), coordinator_id: str = Form('0'),
    university_id: str = Form('0'), semester_label: str = Form(''),
    mobility_type: str = Form(''), duration_semesters: str = Form(''),
    candidate_document: str = Form(''), polimail: str = Form(''),
    intranet_access: str = Form(''), identification: str = Form(''),
    dari_form: str = Form(''), study_plan: str = Form(''),
    responsible_name: str = Form(''), responsible_email: str = Form(''),
    coordination_notes: str = Form(''), registration_semester: str = Form(''),
    notes: str = Form(''), scholarship: str = Form(''), extension: str = Form(''),
    db: Session = Depends(get_db),
):
    record = db.get(Application, application_id)
    if not record or record.direction not in {'incoming', 'outgoing'}:
        raise HTTPException(404, 'Candidatura nao encontrada.')
    _require_edit(request, record.direction)
    if record.status != 'pending':
        return RedirectResponse(f'/candidaturas/ficha/{record.id}', status_code=303)
    imported = db.scalar(select(FormResponse).where(FormResponse.application_id == record.id))
    if imported:
        # v53.48.12: desativar o caminho legado que criava mobilidade a partir
        # do mapeamento automático. Usuário deve abrir Nova candidatura ou
        # Associar e clicar Salvar alterações na ficha padrão.
        return RedirectResponse(f'/candidaturas/ficha/{record.id}', status_code=303)
    draft = _application_draft48_2(db, record)
    name=(name or draft['name']).strip()
    if not name:raise HTTPException(422,'Informe o nome do estudante antes de incluir no banco.')
    if len(name)>220:raise HTTPException(422,'Nome do estudante muito longo (maximo 220 caracteres).')
    if len(email)>180 or len(dre)>30 or len(passport)>60 or len(nationality)>120:
        raise HTTPException(422,'Confira o tamanho dos dados pessoais informados.')
    if len(mobility_type)>100:
        raise HTTPException(422,'Tipo de mobilidade muito longo.')
    available_modalities = {'Duplo Diploma', 'Estágio', 'Intercâmbio Simples', 'Monografia de Pesquisa', 'Projeto Final'}
    original_modality = _application_draft48_2(db, record).get('mobility_type', '')
    if record.direction == 'incoming' and mobility_type not in (available_modalities | {original_modality, ''}):
        raise HTTPException(422,'Selecione uma modalidade apresentada na ficha.')
    if len(extra_key) != len(extra_value) or len(extra_key) > 150:
        raise HTTPException(422,'Respostas adicionais inválidas.')
    expected_extras = {label for label, _ in _application_extras48_2(record)}
    if len(set(extra_key)) != len(extra_key) or set(extra_key) != expected_extras:
        if expected_extras or extra_key:
            raise HTTPException(422,'As respostas adicionais foram alteradas. Reabra a candidatura antes de salvar.')
    if sum(len(x) for x in extra_value) > 200_000:
        raise HTTPException(422,'Respostas adicionais acima do limite aceito.')
    if record.direction == 'incoming' and nationality:
        canonical, _ = _application_country48_3(db, nationality)
        if not canonical:
            raise HTTPException(422,'Escolha um país já cadastrado no sistema.')
        nationality = canonical
    # IDs sempre verificados no catalogo real; nenhuma criacao silenciosa de universidade/curso.
    course_pk=_application_fk48_2(db,Course,course_id)
    university_pk=_application_fk48_2(db,University,university_id)
    program_pk=_application_fk48_2(db,Program,program_id)
    coordinator_pk=_application_fk48_2(db,Coordinator,coordinator_id)
    if record.direction == 'incoming' and start_date_form.strip():
        derived_semester = _application_semester_from_calendar48_3(db, start_date_form)
        original_draft = _application_draft48_2(db, record)
        # Se a data foi alterada e o semestre ainda está no valor pré-preenchido,
        # atualize o semestre. Uma escolha manual explícita prevalece.
        if derived_semester and (semester_label == original_draft['semester_label'] or not semester_label):
            semester_label = derived_semester
    semester_pk=_application_period48_2(db,semester_label)
    try: duration=int(duration_semesters) if duration_semesters.strip() else None
    except (TypeError,ValueError):raise HTTPException(422,'Duracao deve ser um numero de semestres.')
    if duration is not None and not 1 <= duration <= 100:
        raise HTTPException(422,'Duracao deve estar entre 1 e 100 semestres.')
    # Os dados das outras perguntas permanecem no JSON de origem, sem alteracao.
    # So este POST de inclusao executa gravacoes no banco operacional.
    email=email.strip() or None;dre=dre.strip() or None;passport=passport.strip() or None
    student=None
    if passport: student=db.scalar(select(Student).where(Student.passport==passport).limit(1))
    if not student and dre: student=db.scalar(select(Student).where(Student.dre==dre).limit(1))
    if not student and email: student=db.scalar(select(Student).where(func.lower(Student.email)==email.lower()).limit(1))
    if student is None:
        student=Student(full_name=name,email=email,dre=dre,passport=passport,nationality=nationality.strip() or None)
        db.add(student);db.flush()
    else:
        # Nao sobrescrever silenciosamente historicos de estudantes ja cadastrados.
        student.email=student.email or email
        student.dre=student.dre or dre
        student.passport=student.passport or passport
        student.nationality=student.nationality or nationality.strip() or None
    unresolved=[]
    if not course_pk and record.course_name:unresolved.append('Curso da planilha: '+record.course_name)
    if not university_pk and record.institution_name:unresolved.append('Universidade da planilha: '+record.institution_name)
    mobility=Mobility(
        direction=record.direction,student_id=student.id,
        course_id=course_pk,program_id=program_pk,coordinator_id=coordinator_pk,
        home_university_id=university_pk if record.direction=='incoming' else None,
        destination_university_id=university_pk if record.direction=='outgoing' else None,
        start_semester_id=semester_pk,
        mobility_type=mobility_type.strip() or None,
        duration_semesters=duration,status='Candidatura aprovada',
        notes=(notes.strip() or ('; '.join(unresolved) if unresolved else None)),
        scholarship=scholarship.strip() or None,extension=extension.strip() or None,
    )
    db.add(mobility);db.flush()
    if record.direction=='incoming':
        db.add(IncomingDetail(
            mobility_id=mobility.id, candidate_document=candidate_document.strip() or None,
            polimail=polimail.strip() or None, intranet_access=intranet_access.strip() or None,
            identification=identification.strip() or None, dari_form=dari_form.strip() or None,
            study_plan=study_plan.strip() or None,
            responsible_name=responsible_name.strip() or None,
            responsible_email=responsible_email.strip() or None,
            coordination_notes=coordination_notes.strip() or None,
        ))
    else:
        db.add(OutgoingDetail(mobility_id=mobility.id,registration_semester=registration_semester.strip() or None))
    record.status='approved';record.reviewed_at=datetime.now()
    record.reviewed_by=getattr(request.state,'user',{}).get('username')
    record.mobility_id=mobility.id
    record.full_name=name;record.email=email;record.dre=dre;record.passport=passport
    record.country_name=nationality.strip() or None;record.academic_semester=semester_label.strip() or None
    record.application_type=mobility_type.strip() or None
    if course_pk:record.course_name=db.get(Course,course_pk).name
    if university_pk:record.institution_name=db.get(University,university_pk).name
    if imported:
        imported.linked_mobility_id=mobility.id
        extras_to_approve = dict(zip(extra_key, extra_value))
        if record.direction == 'incoming' and start_date_form.strip():
            from .form_bridge_v53_48 import norm
            for key in _application_payload(record):
                if norm(key).startswith('start date format mm yyyy'):
                    extras_to_approve[key] = start_date_form.strip()
                    break
        if extras_to_approve:
            approved_extras = db.scalar(select(FormApprovedExtras).where(FormApprovedExtras.response_id==imported.id))
            if approved_extras is None:
                approved_extras = FormApprovedExtras(response_id=imported.id,
                    fields_json='{}', approved_at=datetime.now().isoformat(timespec='seconds'))
                db.add(approved_extras)
            approved_extras.fields_json=json.dumps(extras_to_approve, ensure_ascii=False)
            approved_extras.approved_at=datetime.now().isoformat(timespec='seconds')
    db.commit()
    return RedirectResponse(f'/mobilidade/{mobility.id}',status_code=303)


@app.post("/candidaturas/{application_id}/rejeitar")
def reject_application(application_id: int, request: Request, rejection_reason: str = Form(""), db: Session = Depends(get_db)):
    record = db.get(Application, application_id)
    if not record or record.direction not in {"incoming", "outgoing"}:
        raise HTTPException(404, "Candidatura não encontrada.")
    _require_edit(request, record.direction)
    if record.status != "pending":
        return RedirectResponse(f"/candidaturas/ficha/{record.id}", status_code=303)
    record.status = "rejected"
    record.decision_note = rejection_reason.strip() or None
    record.reviewed_at = datetime.now()
    record.reviewed_by = getattr(request.state, "user", {}).get("username")
    db.commit()
    return RedirectResponse(f"/candidaturas/{record.direction}/rejeitadas", status_code=303)


@app.post("/candidaturas/{application_id}/reabrir")
def reopen_application(application_id: int, request: Request, db: Session = Depends(get_db)):
    record = db.get(Application, application_id)
    if not record or record.direction not in {"incoming", "outgoing"}:
        raise HTTPException(404, "Candidatura não encontrada.")
    _require_edit(request, record.direction)
    if record.status == "rejected":
        record.status = "pending"
        record.decision_note = None
        record.reviewed_at = None
        record.reviewed_by = None
        db.commit()
    return RedirectResponse(f"/candidaturas/{record.direction}/pendentes", status_code=303)


@app.get("/candidaturas/{application_id}/documento")
def application_document(application_id: int, db: Session = Depends(get_db)):
    record = db.get(Application, application_id)
    if not record:
        raise HTTPException(404, "Candidatura não encontrada.")
    if record.document_local_path:
        candidate = (APPLICATION_FILES_DIR / record.document_local_path).resolve()
        if str(candidate).startswith(str(APPLICATION_FILES_DIR)) and candidate.is_file():
            return FileResponse(candidate, media_type="application/pdf", filename=candidate.name)
    if record.document_url and record.document_url.lower().startswith(("https://", "http://")):
        return RedirectResponse(record.document_url, status_code=307)
    raise HTTPException(404, "PDF da candidatura ainda não está disponível.")



# Incoming — fotografia integral de planilha, consulta local SOMENTE LEITURA.
# As rotas nunca promovem linhas para students/mobilities e não acessam Google.
_INCOMING_SHEET_KINDS = {
    "semester_cohort": "Registro semestral",
    "active_snapshot": "Alunos ativos (histórico)",
    "catalog": "Base auxiliar",
    "double_degree_documents": "Duplo Diploma",
    "unclassified_possible_2019_1": "Aba adicional (conferência)",
    "unclassified_possible_copy_2026_1": "Possível cópia (conferência)",
}


from .university_catalog_v53_22 import canonical_label


def _incoming_json(text, fallback):
    try:
        val = json.loads(text or "null")
        if isinstance(val, dict):
            for institutional_key in ('institution', 'home_university', 'destination_university'):
                if institutional_key in val:
                    val[institutional_key] = canonical_label(val[institutional_key])
        return val if isinstance(val, type(fallback)) else fallback
    except (ValueError, TypeError):
        return fallback


def _incoming_display(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "Sim" if value else "Não"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _incoming_short(text, limit=150):
    text = _incoming_display(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _incoming_row_view(row):
    fields = source_fields30(db,row,_incoming_json(row.field_values_json, {}))
    norm = _incoming_json(row.normalized_json, {})
    original = _incoming_json(row.raw_values_json, [])
    name = fields.get("name") or fields.get("student_name")
    if row.mobility_id:
        with SessionLocal() as current_db:
            current_name=current_db.execute(text('SELECT s.full_name FROM students s JOIN mobilities m ON m.student_id=s.id WHERE m.id=:id'),{'id':row.mobility_id}).scalar()
            if current_name:name=current_name
    if not name and row.row_kind == "catalog_data":
        name = next((x for x in original if x not in (None, "")), None)
    if not name and row.row_kind == "double_degree":
        name = next((x for x in original if isinstance(x, str) and x.strip()), None)
    return {
        "id": row.id, "source_row": row.source_row, "sheet_name": row.sheet_name, "kind": row.row_kind,
        "name": _incoming_short(name, 100),
        "dre": _incoming_display(fields.get("dre")),
        "institution": _incoming_display(fields.get("institution")),
        "course": _incoming_short(fields.get("course"), 95),
        "country": _incoming_display(norm.get("country_proposal") or fields.get("country")),
        "original_country": _incoming_display(fields.get("country")),
        "intranet": _incoming_short(fields.get("intranet_access"), 75),
        "dari_form": _incoming_short(fields.get("dari_form"), 75),
        "notes": _incoming_short(fields.get("coordination_notes") or fields.get("notes") or fields.get("status") or fields.get("coordination_opinion"), 145),
        "review_status": row.review_status,
        "mobility_id": row.mobility_id,
    }


@app.get("/incoming/planilha", response_class=HTMLResponse)
def incoming_spreadsheet(
    request: Request, sheet: str = "2026-2", q: str = "", kind: str = "",
    page: int = 1, db: Session = Depends(get_db),
):
    _require_view(request, "incoming")
    batch = db.scalar(select(IncomingSourceBatch).order_by(IncomingSourceBatch.id.desc()).limit(1))
    ctx = common_context(request, "incoming") | {"active": "incoming_planilha", "batch": batch,
                                                "q": q.strip()[:120], "page": page, "kind": kind,
                                                "sheet": sheet}
    if not batch:
        return render(request, "incoming_source.html", ctx | {
            "sheets": [], "rows": [], "total_rows": 0, "page_count": 0,
            "total_source_rows": 0, "tables": [], "activity_sheets": [],
        })
    sheets = db.scalars(select(IncomingSourceWorksheet).where(
        IncomingSourceWorksheet.batch_id == batch.id).order_by(IncomingSourceWorksheet.sheet_order)).all()
    valid_sheets = {w.sheet_name for w in sheets}
    if sheet != "todas" and sheet not in valid_sheets:
        sheet = "2026-2" if "2026-2" in valid_sheets else (sheets[0].sheet_name if sheets else "todas")
    ctx["sheet"] = sheet
    query = select(IncomingSourceRow).where(IncomingSourceRow.batch_id == batch.id)
    if sheet != "todas":
        query = query.where(IncomingSourceRow.sheet_name == sheet)
    query = query.where(IncomingSourceRow.nonempty.is_(True),
                        IncomingSourceRow.row_kind != "header")
    if kind in {"data", "active_snapshot", "double_degree", "catalog_data", "unclassified_data"}:
        query = query.where(IncomingSourceRow.row_kind == kind)
    else:
        kind = ""
    q = q.strip()[:120]
    if q:
        # Literal contains search; SQLite LIKE escape uses one backslash.
        pattern = "%" + q.replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(
            IncomingSourceRow.field_values_json.ilike(pattern, escape="\\"),
            IncomingSourceRow.raw_values_json.ilike(pattern, escape="\\"),
            IncomingSourceRow.sheet_name.ilike(pattern, escape="\\")))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    size = 45
    pages = math.ceil(total / size)
    page = min(max(1, page), max(1, pages))
    original_rows = db.scalars(query.order_by(
        IncomingSourceRow.sheet_name, IncomingSourceRow.source_row).offset(
        (page - 1) * size).limit(size)).all()
    sheet_tables = db.scalars(select(IncomingSourceTable).where(
        IncomingSourceTable.batch_id == batch.id,
        *((IncomingSourceTable.sheet_name == sheet,) if sheet != "todas" else ())
    ).order_by(IncomingSourceTable.sheet_name, IncomingSourceTable.table_name)).all()
    activities = [w for w in sheets if w.sheet_kind == "active_snapshot"]
    total_source_rows = db.scalar(select(func.count(IncomingSourceRow.id)).where(
        IncomingSourceRow.batch_id == batch.id, IncomingSourceRow.nonempty.is_(True))) or 0
    return render(request, "incoming_source.html", ctx | {
        "sheets": sheets, "rows": [_incoming_row_view(r) for r in original_rows],
        "total_rows": total, "page": page, "page_count": pages,
        "kind": kind, "total_source_rows": total_source_rows,
        "tables": sheet_tables, "activity_sheets": activities,
        "sheet_kind_labels": _INCOMING_SHEET_KINDS,
    })


@app.get("/incoming/planilha/linha/{row_id}", response_class=HTMLResponse)
def incoming_spreadsheet_row(row_id: int, request: Request, db: Session = Depends(get_db)):
    _require_view(request, "incoming")
    row = db.get(IncomingSourceRow, row_id)
    if not row:
        raise HTTPException(404, "Linha não encontrada")
    batch = db.get(IncomingSourceBatch, row.batch_id)
    worksheet = db.scalar(select(IncomingSourceWorksheet).where(
        IncomingSourceWorksheet.batch_id == row.batch_id,
        IncomingSourceWorksheet.sheet_name == row.sheet_name))
    heads = _incoming_json(worksheet.original_headers_json if worksheet else None, [])
    original = _incoming_json(row.raw_values_json, [])
    fields = source_fields30(db,row,_incoming_json(row.field_values_json, {}))
    normalized = _incoming_json(row.normalized_json, {})
    hyperlinks = _incoming_json(row.hyperlink_json, {})
    formulas = _incoming_json(row.formulas_json, {})
    original_columns = []
    for i, val in enumerate(original):
        header = heads[i] if i < len(heads) and heads[i] else f"Coluna {i+1}"
        original_columns.append({"name": header, "value": _incoming_display(val), "col": i+1})
    annotations = db.scalars(select(IncomingSourceAnnotation).where(
        IncomingSourceAnnotation.batch_id == row.batch_id,
        IncomingSourceAnnotation.sheet_name == row.sheet_name,
    ).order_by(IncomingSourceAnnotation.cell)).all()
    # Somente os comentários da mesma linha. A referência é A1/R12, etc.
    import re
    annotations = [a for a in annotations if re.search(r"\d+$", a.cell) and
                   int(re.search(r"\d+$", a.cell).group()) == row.source_row]
    return render(request, "incoming_source_row.html", common_context(request, "incoming") | {
        "active": "incoming_planilha", "row": row, "batch": batch,
        "original_columns": original_columns, "fields": fields, "normalized": normalized,
        "hyperlinks": hyperlinks, "formulas": formulas, "annotations": annotations,
        "display": _incoming_display,
    })


def _incoming_source_display_records(db: Session, q: str, period: str, view: str):
    """Dados ainda nao homologados exibidos NA tabela original de Incoming.

    Sao registros somente-leitura de origem; nunca contam como mobilidades
    operacionais e nunca habilitam Editar/Carta de aceite.
    """
    batch=db.scalar(select(IncomingSourceBatch).order_by(IncomingSourceBatch.id.desc()).limit(1))
    if batch is None or view == 'cadastro':return [], (batch is not None)
    stmt=select(IncomingSourceRow).where(
        IncomingSourceRow.batch_id==batch.id,
        IncomingSourceRow.row_kind=='data',
        IncomingSourceRow.review_status.notin_(['linked_exact', 'linked_high_confidence', 'linked_human_approved','linked_consolidated_v53_33', 'promoted_recent_provisional', 'ignored_manual_v51_nonperson', 'ignored_auxiliary_v53_33']),
    )
    # Aceita somente YYYY/S ou vazio; nunca filtros arbitrarios SQL.
    if period and period != 'todos':stmt=stmt.where(IncomingSourceRow.sheet_name.in_([s.replace('/','-') for s in period.split(',')]))
    if q.strip():
        term=q.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        clauses=[
            IncomingSourceRow.field_values_json.ilike('%'+term+'%',escape='\\'),
            IncomingSourceRow.normalized_json.ilike('%'+term+'%',escape='\\'),
            IncomingSourceRow.sheet_name.ilike('%'+term+'%',escape='\\')]
        ids = matching_universities(db, q.strip())
        if ids:
            old_labels=matching_source_labels(db, ids)
            for alias in sorted(old_labels)[:100]:
                literal=alias.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                clauses.append(IncomingSourceRow.field_values_json.ilike('%'+literal+'%',escape='\\'))
        stmt=stmt.where(or_(*clauses))
    source=db.scalars(stmt.order_by(IncomingSourceRow.sheet_name.desc(),IncomingSourceRow.source_row)).all()
    registry={r["source_row_id"]:dict(r) for r in db.execute(text("""
      SELECT source_row_id, registry_status, candidate_id, review_reason
      FROM incoming_period_registry
    """)).mappings().all()}
    status_labels={
        'provisional_not_yet_official':'Pré-cadastro — a validar',
        'identity_conflict':'Conflito de identidade',
        'potential_historical_duplicate':'Possível duplicação',
        'person_matched_period_unverified':'Pessoa identificada; período a conferir',
        'review_incomplete_identity':'Identificação insuficiente',
        'historical_mobility_confirmed':'Vínculo histórico confirmado',
        'historical_mobility_link_reviewed':'Vínculo aprovado pela DARI',
        'historical_mobility_link_high_confidence':'Vínculo histórico conferido',
    }
    records=[]
    for row in source:
        fields=source_fields30(db,row,_incoming_json(row.field_values_json,{}))
        if registry.get(row.id, {}).get('registry_status') == 'non_person_excluded_v51':
            # Decisão humana v51: marcador da planilha, não é estudante.
            continue
        if str(fields.get('name') or '').strip().casefold() in ('','nome','name','full name'):
            # Linhas auxiliares sem pessoa identificavel continuam preservadas
            # na auditoria da planilha, mas nao viram linhas da tabela de alunos.
            continue
        norm=_incoming_json(row.normalized_json,{})
        records.append({
            'id':row.id,'row':row.source_row,'sheet':row.sheet_name,
            'registry_status':registry.get(row.id,{}).get('registry_status','review_incomplete_identity'),
            'registry_label':status_labels.get(registry.get(row.id,{}).get('registry_status'),'Em conciliação'),
            'candidate_id':registry.get(row.id,{}).get('candidate_id'),
            'registry_reason':registry.get(row.id,{}).get('review_reason'),
            'dre_is_pending':not bool(__import__('re').fullmatch(r'\d{9,10}', str(fields.get('dre') or '').strip())),
            'semester':row.sheet_name.replace('-','/'),
            'name':_incoming_display(fields.get('name')),
            'dre':_incoming_display(fields.get('dre')),
            'passport':_incoming_display(fields.get('passport')),
            'email':_incoming_display(fields.get('email')),
            'institution':_incoming_display(fields.get('institution')),
            'country':_incoming_display(norm.get('country_proposal') or fields.get('country')),
            'course':_incoming_display(fields.get('course')),
            'program':_incoming_display(fields.get('program')),
            'coordinator':_incoming_display(fields.get('coordinator')),
            'duration':_incoming_display(fields.get('duration_text')),
            'mobility_type':modality29(_incoming_display(fields.get('mobility_type'))),
            # texto livre NAO vira situacao administrativa inferida.
            'status':_incoming_display(fields.get('status')),
            'polimail':_incoming_display(fields.get('polimail')),
            'responsible_name':_incoming_display(fields.get('responsible_name')),
            'responsible_email':_incoming_display(fields.get('responsible_email')),
            'candidate_document':_incoming_display(fields.get('candidate_document')),
            'intranet_access':_incoming_display(fields.get('intranet_access')),
            'dari_form':_incoming_display(fields.get('dari_form')),
            'study_plan':_incoming_display(fields.get('study_plan')),
            'notes':_incoming_display(fields.get('coordination_notes') or fields.get('notes') or fields.get('coordination_opinion')),
        })
    return records,True


@app.get("/incoming/pre-cadastro/{candidate_id}", response_class=HTMLResponse)
def incoming_candidate_review(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    _require_view(request,'incoming')
    candidate=db.execute(text("""SELECT id,full_name,dre_text,identity_method,review_status
        FROM incoming_identity_candidates WHERE id=:id"""),{'id':candidate_id}).mappings().first()
    if candidate is None:
        raise HTTPException(status_code=404,detail='Pré-cadastro não encontrado')
    if candidate['review_status']=='operational_consolidated_v53_33':
        mids=db.execute(text('SELECT DISTINCT linked_mobility_id FROM incoming_period_registry WHERE candidate_id=:id AND linked_mobility_id IS NOT NULL'),{'id':candidate_id}).scalars().all()
        if len(mids)==1:return RedirectResponse('/mobilidade/'+str(mids[0]),status_code=303)
    source=db.execute(text("""SELECT sr.id,sr.sheet_name,sr.source_row,sr.field_values_json,
          pr.registry_status,pr.source_semester
        FROM incoming_period_registry pr JOIN incoming_source_rows sr ON sr.id=pr.source_row_id
        WHERE pr.candidate_id=:id ORDER BY sr.sheet_name,sr.source_row"""),{'id':candidate_id}).mappings().all()
    details=[]
    for r in source:
        f=source_fields30(db,db.get(IncomingSourceRow,r['id']),_incoming_json(r['field_values_json'],{}))
        details.append({'id':r['id'],'sheet':r['sheet_name'],'row':r['source_row'],
                        'semester':r['source_semester'],'status':r['registry_status'],
                        'institution':_incoming_display(f.get('institution')),
                        'country':_incoming_display(f.get('country')),
                        'form_dari':_incoming_display(f.get('dari_form')),
                        'intranet':_incoming_display(f.get('intranet_access')),
                        'notes':_incoming_display(f.get('notes') or f.get('coordination_notes'))})
    return render(request,'incoming_candidate.html',common_context(request,'incoming') | {
        'candidate':candidate,'candidate_rows':details,
    })

@app.get("/incoming/pendencias", response_class=HTMLResponse)
def incoming_migration_review(request: Request, db: Session = Depends(get_db)):
    _require_view(request,'incoming')
    counts=dict(db.execute(text("""SELECT registry_status,count(*) FROM incoming_period_registry
                                    GROUP BY registry_status""")).all())
    conflicts=db.execute(text("""SELECT pr.registry_status,pr.review_reason,sr.id,sr.sheet_name,
          sr.source_row,sr.field_values_json
        FROM incoming_period_registry pr JOIN incoming_source_rows sr ON sr.id=pr.source_row_id
        WHERE pr.registry_status IN ('identity_conflict','potential_historical_duplicate','review_incomplete_identity')
        ORDER BY sr.sheet_name DESC,sr.source_row LIMIT 300""")).mappings().all()
    review_rows=[]
    for x in conflicts:
        f=_incoming_json(x['field_values_json'],{})
        review_rows.append({'id':x['id'],'sheet':x['sheet_name'],'row':x['source_row'],
                            'name':_incoming_display(f.get('name')),
                            'dre':_incoming_display(f.get('dre')),
                            'university':_incoming_display(f.get('institution')),
                            'status':x['registry_status'],'reason':x['review_reason']})
    country_review=db.execute(text("""SELECT source_value,proposed_canonical,review_reason
       FROM incoming_country_normalization WHERE confidence='review' ORDER BY source_value""")).mappings().all()
    universities_review=db.execute(text("""SELECT n.source_value,n.suggestion,n.review_reason
       FROM incoming_university_normalization AS n
       WHERE n.confidence LIKE 'review%'
       AND NOT EXISTS (
          SELECT 1 FROM incoming_v51_university_aliases AS a
          WHERE LOWER(TRIM(a.source_value)) = LOWER(TRIM(n.source_value))
       )
       ORDER BY n.source_value""")).mappings().all()
    return render(request,'incoming_pending_review.html',common_context(request,'incoming') | {
        'review_rows':review_rows,'review_countries':country_review,
        'review_universities':universities_review,'migration_counts':counts,
    })



def _incoming_v49_university_labels(db: Session):
    """Alias aprovado da mobilidade (nao altera o nome global da universidade)."""
    from sqlalchemy import inspect
    if not inspect(db.get_bind()).has_table('incoming_v49_operational_corrections'):
        return {}
    return {mid: canonical_label(label) for mid,label in db.execute(text("""
        SELECT mobility_id,approved_university_name
        FROM incoming_v49_operational_corrections
        WHERE application_status='applied'
    """)).all()}

# v50: cadastros operacionais importados, mas sem homologação de atividade.
_INCOMING_RECENT_PENDING = 'Pré-cadastro — planilha (não homologado)'


def _incoming_recent_info(db: Session, mobility_ids=None):
    from sqlalchemy import inspect
    if not inspect(db.get_bind()).has_table('incoming_recent_promotions'):
        return {}
    stmt = """SELECT mobility_id,source_row_id,source_semester,source_university,
                     source_country,canonical_country,source_course,source_program,source_coordinator,
                     source_admin_status,normalized_administrative_status,confidence
              FROM incoming_recent_promotions"""
    params={}
    if mobility_ids is not None:
        if not mobility_ids:return {}
        placeholders=','.join(':'+str(i) for i in range(len(mobility_ids)))
        stmt+=' WHERE mobility_id IN ('+placeholders+')'
        params={str(i):mid for i,mid in enumerate(mobility_ids)}
    result={r['mobility_id']:dict(r) for r in db.execute(text(stmt),params).mappings().all()}
    result={mid:info for mid,info in result.items() if (db.get(Mobility,mid) and db.get(Mobility,mid).status==_INCOMING_RECENT_PENDING)}
    for mid,info in result.items():
        if not edited30(db,'mobility',mid):continue
        m=db.get(Mobility,mid)
        if not m:continue
        info.update(source_university=m.home_university.name if m.home_university else '',
                    canonical_country=m.home_university.country.name if m.home_university and m.home_university.country else '',
                    source_course=m.course.name if m.course else '',
                    source_program=m.program.name if m.program else '',
                    source_coordinator=m.coordinator.name if m.coordinator else '')
    return result


def _incoming_recent_is_provisional(db: Session, mobility_id: int) -> bool:
    return mobility_id in _incoming_recent_info(db,[mobility_id])


from .mobility_periods_v53_29 import listing as listing29, period_filter as semester29, modality as modality29, ensure_semester as ensure_semester29

@app.get("/incoming", response_class=HTMLResponse)
def incoming(request: Request, q: str = "", candidatura: str = "", periodo: str = "todos", origem: str = "todos", situacao: str = "todos", processo: str = "todos", db: Session = Depends(get_db)):
    _require_view(request,"incoming")
    origem='todos'
    periodo=semester29(periodo) or 'todos'
    stmt=select(Mobility).options(
        joinedload(Mobility.student),joinedload(Mobility.course),joinedload(Mobility.program),
        joinedload(Mobility.coordinator),joinedload(Mobility.home_university).joinedload(University.country),
        joinedload(Mobility.start_semester),joinedload(Mobility.incoming_detail)
    ).where(Mobility.direction=='incoming', _mobility_not_deleted())
    stmt=_apply_mobility_search(stmt,'incoming',q,db)
    if origem == 'planilha':
        stmt=stmt.where(Mobility.status == 'Pré-cadastro — planilha (não homologado)')
    operational=db.scalars(stmt.order_by(Mobility.id.desc())).unique().all()
    staged,available=_incoming_source_display_records(db,q,periodo,origem)
    provenance={}
    if operational:
        mids=[m.id for m in operational]
        for src in db.scalars(select(IncomingSourceRow).where(
            IncomingSourceRow.mobility_id.in_(mids),
            IncomingSourceRow.review_status.in_(['linked_exact', 'linked_high_confidence', 'linked_human_approved','linked_consolidated_v53_33', 'promoted_recent_provisional']))).all():
            provenance[src.mobility_id]=src.id
    recent_provisional = _incoming_recent_info(db, [m.id for m in operational])
    # DARI_V53_16_LISTA_INCOMING
    situacao='todos'
    processo='concluidos' if processo=='concluidos' else 'todos'
    operational, status_context = listing29(db, operational, periodo, processo, situacao)
    workflow_state_map = status_context['workflow_state_map']
    processo = status_context['processo']
    if processo == 'concluidos' or situacao != 'todos':
        staged = []
    status_context['status_counts']['todos'] += len(staged)
    recent_semester_labels = {}
    if recent_provisional:
        from sqlalchemy import inspect
        if inspect(db.get_bind()).has_table('incoming_recent_semester_evidence'):
            ids=list(recent_provisional)
            named=','.join(':'+str(i) for i in range(len(ids)))
            for mid,semester in db.execute(text(
                'SELECT DISTINCT mobility_id,semester_label FROM incoming_recent_semester_evidence '
                'WHERE mobility_id IN ('+named+')'), {str(i):v for i,v in enumerate(ids)}).all():
                recent_semester_labels.setdefault(mid,set()).add(semester)
            recent_semester_labels={mid:' / '.join(sorted(v)) for mid,v in recent_semester_labels.items()}
    approved_university_labels = {mid:label for mid,label in _incoming_v49_university_labels(db).items() if not edited30(db,'mobility',mid)}
    approved_university_labels.update({mid: canonical_label(inf['source_university'])
                                        for mid,inf in recent_provisional.items() if inf['source_university']})
    from .incoming_consolidation_v53_33 import attach
    consolidated=attach(db,operational)
    approved_university_labels.update({m.id:m.home_university.name if m.home_university else '' for m in operational if m.id in consolidated})
    # Semestres enumerados a partir do conteudo de origem e do banco legado.
    source_semesters=[]
    if available:
        source_semesters=[r[0].replace('-','/') for r in db.execute(
            select(IncomingSourceRow.sheet_name).where(
                IncomingSourceRow.row_kind=='data',
                IncomingSourceRow.sheet_name.like('20__-_')).distinct()).all()]
    existing_semesters=[r[0] for r in db.execute(select(Semester.label).order_by(Semester.year.desc(),Semester.term.desc())).all()]
    semesters=sorted(set(source_semesters+existing_semesters),reverse=True)
    status_context['semesters_incoming']=sorted(set(status_context['semesters_incoming']) | set(semesters),reverse=True)
    return render(request,'incoming.html',common_context(request,'incoming') | {
        'rows':operational,'source_records':staged,'source_available':available,'incoming_profiles33':consolidated,
        'source_provenance':provenance,'q':q,'periodo':periodo,'origem':origem,
        'semesters_incoming':semesters,'candidatura':candidatura,
        'workflow_state_map':workflow_state_map,'processo':processo,
        'approved_university_labels':approved_university_labels,
        'recent_provisional':recent_provisional,
        'recent_semester_labels':recent_semester_labels,
    } | status_context)

@app.get("/outgoing", response_class=HTMLResponse)
def outgoing(request: Request, q: str = "", candidatura: str = "", periodo: str = "todos", situacao: str = "todos", processo: str = "todos", db: Session = Depends(get_db)):
    _require_view(request, "outgoing")
    stmt = select(Mobility).options(
        joinedload(Mobility.student), joinedload(Mobility.course), joinedload(Mobility.program),
        joinedload(Mobility.coordinator), joinedload(Mobility.destination_university).joinedload(University.country),
        joinedload(Mobility.start_semester), joinedload(Mobility.outgoing_detail)
    ).where(Mobility.direction=="outgoing", _mobility_not_deleted())
    stmt = _apply_mobility_search(stmt, "outgoing", q)
    rows = db.scalars(stmt.order_by(Mobility.id.desc())).unique().all()
    # DARI_V53_16_LISTA_OUTGOING
    situacao='todos'
    processo='concluidos' if processo=='concluidos' else 'todos'
    rows, status_context = listing29(db, rows, periodo, processo, situacao)
    return render(request, "outgoing.html", common_context(request, "outgoing") | {"rows": rows, "q": q, "candidatura": candidatura} | status_context)

def mobility_query_options():
    return (
        joinedload(Mobility.student), joinedload(Mobility.course), joinedload(Mobility.program),
        joinedload(Mobility.coordinator), joinedload(Mobility.home_university).joinedload(University.country),
        joinedload(Mobility.destination_university).joinedload(University.country),
        joinedload(Mobility.start_semester), joinedload(Mobility.end_semester),
        joinedload(Mobility.incoming_detail), joinedload(Mobility.outgoing_detail),
    )


def mobility_form_context(request: Request, db: Session, direction: str, mobility: Mobility | None = None):
    from .mobility_presentation_v53_34 import context
    return common_context(request, direction) | context(db,mobility) | schedule_context40(db,mobility) | {
        "direction": direction,
        "mobility": mobility,
        "courses": db.scalars(select(Course).order_by(Course.name)).all(),
        "programs": db.scalars(select(Program).order_by(Program.name)).all(),
        "coordinators": db.scalars(select(Coordinator).order_by(Coordinator.name)).all(),
        "universities": db.scalars(select(University).order_by(University.name)).all(),
        "semesters": db.scalars(select(Semester).order_by(Semester.year.desc(), Semester.term.desc())).all(),
    }


# DARI v53.48.9 - documentos particulares do processo Incoming/Outgoing.
# Usa o repositorio binario/sincronizavel v53.47; nao guarda caminhos locais do Windows.
_STUDENT_DOCUMENT_ROOT = 'Estudantes'
_STUDENT_DOCUMENT_BLOCKED_EXTENSIONS = {
    '.exe', '.bat', '.cmd', '.com', '.ps1', '.vbs', '.js', '.mjs', '.html',
    '.htm', '.svg', '.msi', '.dll', '.scr', '.lnk', '.sh', '.py', '.jar'
}


def _student_documents_folder(mobility_id: int) -> str:
    return f'{_STUDENT_DOCUMENT_ROOT}/Processo-{int(mobility_id)}'


def _student_document_csrf(request: Request, mobility_id: int, operation: str) -> str:
    # Token vinculado ao cookie, a mobilidade e a operacao; evita POSTs de origem externa.
    cookie = request.cookies.get(AUTH_COOKIE, '')
    message = f'student-doc-v53-48-9:{mobility_id}:{operation}:{cookie}'
    return hmac.new(DEMO_SECRET.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()


def _student_document_require_csrf(request: Request, mobility_id: int, operation: str, token: str):
    if not request.cookies.get(AUTH_COOKIE) or not hmac.compare_digest(
        token, _student_document_csrf(request, mobility_id, operation)
    ):
        raise HTTPException(403, 'Formulário expirado ou inválido. Abra a ficha novamente.')


def _student_document_records(db: Session, mobility: Mobility):
    folder = _student_documents_folder(mobility.id)
    # Nao cria pastas (nem gera alteracoes) simplesmente por visualizar a ficha.
    if document_repo.get(db, mobility.direction, folder) is None:
        return []
    return [row for row in document_repo.list_children(db, mobility.direction, folder)
            if row.kind == 'file']


def _student_document_row(db: Session, mobility: Mobility, doc_id: str):
    if len(doc_id) != 32 or any(c not in '0123456789abcdef' for c in doc_id):
        raise HTTPException(404, 'Documento não encontrado.')
    row = document_repo.get_id(db, doc_id)
    if not row or row.deleted or row.kind != 'file' or row.area != mobility.direction:
        raise HTTPException(404, 'Documento não encontrado.')
    if row.path.rsplit('/', 1)[0] != _student_documents_folder(mobility.id):
        raise HTTPException(404, 'Documento não pertence a este processo.')
    return row


def _student_document_mobility(db: Session, mobility_id: int, request: Request, *, edit: bool):
    mobility = db.get(Mobility, mobility_id)
    if not mobility or mobility.direction not in ('incoming', 'outgoing') or _mobility_is_deleted(db, mobility_id):
        raise HTTPException(404, 'Processo não encontrado.')
    if edit:
        _require_edit(request, mobility.direction)
    else:
        _require_view(request, mobility.direction)
    return mobility


@app.post('/mobilidade/{mobility_id}/documentos-estudante/adicionar')
def student_document_upload(
    mobility_id: int, request: Request, titulo: str = Form(...),
    csrf: str = Form(...), arquivo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    mobility = _student_document_mobility(db, mobility_id, request, edit=True)
    _student_document_require_csrf(request, mobility_id, 'upload', csrf)
    title = (titulo or '').strip()
    if not title or len(title) > 130:
        raise HTTPException(422, 'Informe um título de até 130 caracteres.')
    # Conserva a extensao original sem permitir nomes de sistema ou execucao direta.
    original = Path((arquivo.filename or '').replace('\\', '/')).name
    if not original:
        raise HTTPException(422, 'Selecione um arquivo.')
    suffix = Path(original).suffix.lower()
    if suffix in _STUDENT_DOCUMENT_BLOCKED_EXTENSIONS:
        raise HTTPException(415, 'Tipo de arquivo não permitido por segurança.')
    safe_title = document_repo.clean_name(title)
    if suffix and safe_title.casefold().endswith(suffix.casefold()):
        safe_title = safe_title[:-len(suffix)]
    filename = document_repo.clean_name(safe_title + suffix)
    try:
        content = arquivo.file.read(document_repo.MAX_FILE_BYTES + 1)
        if len(content) > document_repo.MAX_FILE_BYTES:
            raise HTTPException(413, f'Arquivo acima do limite de {document_repo.MAX_FILE_BYTES // (1024 * 1024)} MB.')
    finally:
        arquivo.file.close()
    # As pastas sao criadas somente no primeiro envio; cada processo tem seu prefixo.
    for parent, name in (('', _STUDENT_DOCUMENT_ROOT),
                         (_STUDENT_DOCUMENT_ROOT, f'Processo-{mobility.id}')):
        full = name if not parent else parent + '/' + name
        if document_repo.get(db, mobility.direction, full) is None:
            document_repo.create_folder(db, mobility.direction, parent, name)
    document_repo.create_file(db, mobility.direction, _student_documents_folder(mobility.id), filename, content)
    # Compartilhamento com outras maquinas ocorrera apenas se a API central estiver disponivel.
    sync = synchronize_documents(db, force=True)
    state = 'sincronizado' if sync.get('state') == 'sincronizado' else 'salvo_local'
    if 'application/json' in request.headers.get('accept', ''):
        return {'ok': True, 'state': state}
    return RedirectResponse(f'/mobilidade/{mobility_id}/editar?doc_estado={state}#documentos-estudante', 303)


@app.get('/mobilidade/{mobility_id}/documentos-estudante/editar-fragmento', response_class=HTMLResponse)
def student_document_edit_fragment(mobility_id: int, request: Request,
                                  db: Session = Depends(get_db)):
    mobility = _student_document_mobility(db, mobility_id, request, edit=True)
    return render(request, '_student_documents_v53_48_10.html', {
        'request': request,
        'm': mobility,
        'student_documents': _student_document_records(db, mobility),
        'student_documents_edit': True,
        'student_doc_upload_csrf': _student_document_csrf(request, mobility.id, 'upload'),
        'student_doc_delete_csrf': _student_document_csrf(request, mobility.id, 'delete'),
    })


@app.get('/mobilidade/{mobility_id}/documentos-estudante/{doc_id}')
def student_document_download(mobility_id: int, doc_id: str, request: Request,
                              db: Session = Depends(get_db)):
    mobility = _student_document_mobility(db, mobility_id, request, edit=False)
    # Recolhe novos documentos do central quando disponivel (sem bloquear offline).
    synchronize_documents(db)
    row = _student_document_row(db, mobility, doc_id)
    if row.body is None:
        raise HTTPException(404, 'Conteúdo do documento não disponível neste computador.')
    from io import BytesIO
    import mimetypes
    name = row.path.rsplit('/', 1)[-1]
    media = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    return StreamingResponse(BytesIO(row.body), media_type=media,
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(name, safe='')}",
                 'Content-Length': str(len(row.body)), 'X-Content-Type-Options': 'nosniff'})


@app.post('/mobilidade/{mobility_id}/documentos-estudante/{doc_id}/excluir')
def student_document_delete(mobility_id: int, doc_id: str, request: Request,
                            csrf: str = Form(...), db: Session = Depends(get_db)):
    mobility = _student_document_mobility(db, mobility_id, request, edit=True)
    _student_document_require_csrf(request, mobility_id, 'delete', csrf)
    row = _student_document_row(db, mobility, doc_id)
    document_repo.delete(db, mobility.direction, row.path)
    sync = synchronize_documents(db, force=True)
    state = 'sincronizado' if sync.get('state') == 'sincronizado' else 'salvo_local'
    if 'application/json' in request.headers.get('accept', ''):
        return {'ok': True, 'state': state}
    return RedirectResponse(f'/mobilidade/{mobility_id}/editar?doc_estado={state}#documentos-estudante', 303)


@app.get("/mobilidade/{mobility_id}", response_class=HTMLResponse)
def mobility_detail(mobility_id: int, request: Request, db: Session = Depends(get_db)):
    m = db.scalar(select(Mobility).options(*mobility_query_options()).where(Mobility.id == mobility_id, _mobility_not_deleted()))
    if not m:
        return RedirectResponse("/", status_code=303)
    _require_view(request, m.direction)
    incoming_source_origin = None
    if m.direction == 'incoming':
        incoming_source_origin = db.scalar(select(IncomingSourceRow).where(
            IncomingSourceRow.mobility_id == m.id,
            IncomingSourceRow.review_status.in_(['linked_exact', 'linked_high_confidence', 'linked_human_approved','linked_consolidated_v53_33', 'promoted_recent_provisional'])).limit(1))
    incoming_supplement=None
    if m.direction=='incoming':
        incoming_supplement=db.execute(text("""SELECT * FROM incoming_operational_supplements WHERE mobility_id=:mid"""),
                                       {'mid':m.id}).mappings().first()
    recent_provisional=_incoming_recent_info(db,[m.id]).get(m.id) if m.direction=='incoming' else None
    from .incoming_consolidation_v53_33 import detail as consolidated_detail
    consolidation=consolidated_detail(db,m) if m.direction=="incoming" else {}
    from .outgoing_consolidation_v53_38 import attach as outgoing_attach38
    outgoing_attach38(db,[m])
    from .mobility_presentation_v53_34 import context as presentation_context34
    student_document_sync = synchronize_documents(db)
    student_documents = _student_document_records(db, m)
    return render(request, "mobility_detail.html", common_context(request, m.direction) | {
        "return_to": safe_back(request.query_params.get("voltar", ""), ""),
        "m": m, "workflow_situation": _workflow_state_of(db, m.id), "incoming_source_origin": incoming_source_origin,
        "incoming_recent_provisional":recent_provisional,
        "incoming_supplement": incoming_supplement,
        "form_responses48": mobility_extras(db, m.id),
        "student_documents": student_documents,
        "student_document_sync": student_document_sync,
        "student_doc_status": request.query_params.get('doc_estado', ''),
        "incoming_approved_university_name":(
            (m.home_university.name if m.home_university else '') if (edited30(db,'mobility',m.id) or consolidation) else canonical_label((recent_provisional or {}).get('source_university') or _incoming_v49_university_labels(db).get(m.id))
        ) if m.direction=='incoming' else None,
    } | consolidation | presentation_context34(db,m) | schedule_context40(db,m))


@app.get('/mobilidade/{mobility_id}/documentos/carta-aceite.docx')
@app.get('/mobilidade/{mobility_id}/documentos/carta-aceite.pdf')
def acceptance_legacy_redirect46(mobility_id:int):
    return RedirectResponse(f'/mobilidade/{mobility_id}/documentos/carta-aceite',303)


@app.get('/mobilidade/{mobility_id}/documentos/carta-aceite',response_class=HTMLResponse)
def acceptance_form46(mobility_id:int,request:Request,db:Session=Depends(get_db)):
    _require_view(request,'incoming')
    m=db.scalar(select(Mobility).options(*mobility_query_options()).where(Mobility.id==mobility_id,Mobility.direction=='incoming'))
    if not m:raise HTTPException(404,'Estudante Incoming não encontrado.')
    if _incoming_recent_is_provisional(db,mobility_id):raise HTTPException(409,'Documento bloqueado: cadastro ainda não homologado.')
    from .acceptance_v53_46 import labs_for_letter, default_letter_values
    return render(request,'acceptance_form_v53_46.html',common_context(request,'incoming')|{'m':m,'labs46':labs_for_letter(db),'values46':default_letter_values(m)})


@app.post('/mobilidade/{mobility_id}/documentos/carta-aceite/{formato}')
async def acceptance_generate46(mobility_id:int,formato:str,request:Request,db:Session=Depends(get_db)):
    _require_view(request,'incoming')
    if formato not in ('docx','pdf'):raise HTTPException(404)
    m=db.scalar(select(Mobility).options(*mobility_query_options()).where(Mobility.id==mobility_id,Mobility.direction=='incoming'))
    if not m:raise HTTPException(404,'Estudante Incoming não encontrado.')
    if _incoming_recent_is_provisional(db,mobility_id):raise HTTPException(409,'Documento bloqueado: cadastro ainda não homologado.')
    from .acceptance_v53_46 import generate, prepare_letter
    form=await request.form()
    try:
        record=prepare_letter(m,dict(form),db)
        content=generate(record, BASE_DIR/'static'/('cabecalho_carta_laboratorio_v53_46.jpg' if record['type']=='laboratorio' else 'cabecalho_carta_geral_v53_46.png'),formato)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    suffix='Laboratorio' if record['type']=='laboratorio' else 'Geral'
    filename=f'Carta_Aceite_{suffix}_{safe_filename(m.student.full_name)}.{formato}'
    media='application/pdf' if formato=='pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return StreamingResponse(iter([content]),media_type=media,headers={'Content-Disposition':f'attachment; filename="{filename}"'})


@app.get("/mobilidade/{mobility_id}/editar", response_class=HTMLResponse)
def edit_mobility_form(mobility_id: int, request: Request, form_response: int = 0, db: Session = Depends(get_db)):
    m = db.scalar(select(Mobility).options(*mobility_query_options()).where(Mobility.id == mobility_id, _mobility_not_deleted()))
    if not m:
        return RedirectResponse("/", status_code=303)
    _require_edit(request, m.direction)
    from .outgoing_consolidation_v53_38 import attach as outgoing_attach38
    outgoing_attach38(db,[m])
    synchronize_documents(db)
    editor_context = mobility_form_context(request, db, m.direction, m)
    editor_context.update({
        'm': m,
        'student_documents': _student_document_records(db, m),
        'student_documents_edit': True,
        'student_doc_upload_csrf': _student_document_csrf(request, m.id, 'upload'),
        'student_doc_delete_csrf': _student_document_csrf(request, m.id, 'delete'),
        'mobility_delete_csrf': _student_document_csrf(request, m.id, 'delete-process'),
        'form_cards48_12':editable_form_cards48_12(db,mobility_id=m.id,pending_response_id=form_response or None,direction=m.direction),
        'pending_form_response48_12':form_response,
    })
    return render(request, "edit_mobility.html", editor_context)


@app.post("/mobilidade/{mobility_id}/editar")
def update_mobility(
    mobility_id: int, request: Request,
    name: str = Form(...), email: str = Form(""), dre: str = Form(""), passport: str = Form(""),
    course_id: int = Form(...), program_id: int = Form(...), coordinator_id: int = Form(...),
    university_id: int = Form(...), semester_label: str = Form(""), mobility_type: str = Form(...),
    period_priority: str = Form("duration"), period_mode: str = Form("semesters"), mobility_start_date: str = Form(""), mobility_end_date: str = Form(""),
    duration_semesters: str = Form(""), status: str = Form(""),
    status_choice: str = Form(""), activity_control: str = Form(""), is_active: str = Form(""),
    # Incoming
    candidate_document: str = Form(""), polimail: str = Form(""), coordination_notes: str = Form(""),
    intranet_access: str = Form(""), identification: str = Form(""), dari_form: str = Form(""),
    study_plan: str = Form(""), responsible_name: str = Form(""), responsible_email: str = Form(""),
    # Outgoing
    expected_return_period: str = Form(""), expected_return: str = Form(""), scholarship: str = Form(""),
    extension: str = Form(''), notes: str = Form(''),
    pending_form_response: int = Form(0), form_field_key: list[str] = Form([]),
    form_field_value: list[str] = Form([]), db: Session = Depends(get_db),
):
    m = db.scalar(select(Mobility).options(*mobility_query_options()).where(Mobility.id == mobility_id, _mobility_not_deleted()))
    if not m:
        raise HTTPException(404, 'Processo não encontrado.')
    _require_edit(request, m.direction)

    from .mobility_presentation_v53_34 import save,context
    old_presentation=context(db,m)
    from .mobility_status_v53_35 import validate as validate_status35,apply as apply_status35
    try:validate_status35(status_choice,status)
    except ValueError as exc:raise HTTPException(422,str(exc))
    status_color='gray' if status_choice=='custom' else ''
    try:
        plan40=prepare_schedule40(db,period_mode,semester_label,duration_semesters,mobility_start_date,mobility_end_date,expected_return_period,period_priority)
        semester_label=plan40['first']
        semester_id=ensure_semester29(db,semester_label,0)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422,str(exc))
    original_status35=m.status
    before30=snapshot30(m)|{"student":snapshot30(m.student),"status_color":old_presentation['selected_color34'],"is_active":old_presentation['mobility_is_active34'],"schedule":schedule_context40(db,m)["schedule40"]}
    m.student.full_name = name.strip()
    m.student.email = email.strip() or None
    m.student.dre = dre.strip() or None
    m.student.passport = passport.strip() or None
    m.course_id = course_id or None
    m.program_id = program_id or None
    m.coordinator_id = coordinator_id or None
    m.start_semester_id = semester_id
    m.mobility_type = modality29(mobility_type) or None
    m.duration_semesters = plan40['duration']
    m.status = status.strip() or None
    m.notes = notes.strip() or None

    if m.direction == "incoming":
        m.home_university_id = university_id or None
        if not m.incoming_detail:
            m.incoming_detail = IncomingDetail(mobility_id=m.id)
        d = m.incoming_detail
        d.candidate_document = candidate_document.strip() or None
        d.polimail = polimail.strip() or None
        d.coordination_notes = coordination_notes.strip() or None
        d.intranet_access = intranet_access.strip() or None
        d.identification = identification.strip() or None
        d.dari_form = dari_form.strip() or None
        d.study_plan = study_plan.strip() or None
        d.responsible_name = responsible_name.strip() or None
        d.responsible_email = responsible_email.strip() or None
    else:
        m.destination_university_id = university_id or None
        m.scholarship = scholarship.strip() or None
        m.extension = extension.strip() or None
        if not m.outgoing_detail:
            m.outgoing_detail = OutgoingDetail(mobility_id=m.id)
        d = m.outgoing_detail
    save_schedule40(db,m,plan40)
    db.flush()

    m.status=original_status35
    desired_active=status_choice=='active'
    apply_status35(db,m,status_choice,old_presentation['status_choice35'],status,desired_active,old_presentation['mobility_is_active34'],request.state.user['username'])
    save(db,m.id,status_color)
    mark30(db,'mobility',m.id,request.state.user['username'],before30,snapshot30(m)|{"student":snapshot30(m.student),"status_color":status_color,"is_active":desired_active,"schedule":plan40})
    save_form_cards48_12(db,m,request.state.user['username'],pending_form_response,form_field_key,form_field_value)
    db.commit()
    return RedirectResponse(f"/mobilidade/{m.id}", status_code=303)


@app.post('/mobilidade/{mobility_id}/excluir')
def exclude_mobility_record_v53_48_11(
    mobility_id: int, request: Request,
    csrf: str = Form(...), db: Session = Depends(get_db),
):
    mobility = db.get(Mobility, mobility_id)
    if not mobility or mobility.direction not in ('incoming', 'outgoing') or _mobility_is_deleted(db, mobility_id):
        raise HTTPException(404, 'Processo não encontrado.')
    _require_edit(request, mobility.direction)
    _student_document_require_csrf(request, mobility_id, 'delete-process', csrf)
    db.execute(text("""
        INSERT INTO dari_deleted_mobilities_v53_48_11(mobility_id, deleted_by)
        VALUES (:mid, :username)
    """), {
        'mid': mobility.id,
        'username': request.state.user['username'],
    })
    db.commit()
    return RedirectResponse('/' + mobility.direction + '?registro_excluido=1', status_code=303)


def _agreement_visible46():
    # Exclusão administrativa reversível: referências externas e rastreabilidade são preservadas.
    return Agreement.id.not_in(select(column('agreement_id')).select_from(table('dari_deleted_agreements_v53_46')))


def _agreement_token46(request):
    return hmac.new(DEMO_SECRET.encode('utf-8'),('agreement-delete46:'+request.cookies.get(AUTH_COOKIE,'')).encode('utf-8'),hashlib.sha256).hexdigest()


@app.post('/acordos/item/{agreement_id}/excluir')
def delete_agreement46(agreement_id:int, request:Request, csrf:str=Form(...), db:Session=Depends(get_db)):
    _require_edit(request,'acordos')
    if not request.cookies.get(AUTH_COOKIE) or not hmac.compare_digest(csrf,_agreement_token46(request)):
        raise HTTPException(403,'Sessão inválida. Reabra a ficha.')
    target=db.scalar(select(Agreement).where(Agreement.id==agreement_id,_agreement_visible46()))
    if not target:raise HTTPException(404,'Acordo não encontrado ou já excluído.')
    username=request.state.user['username']
    db.execute(text('INSERT INTO dari_deleted_agreements_v53_46(agreement_id,deleted_by) VALUES (:id,:user)'),{'id':agreement_id,'user':username})
    db.commit()
    return RedirectResponse('/acordos?excluido=1',status_code=303)


@app.get("/acordos", response_class=HTMLResponse)
def agreements(request: Request, q: str = "", db: Session = Depends(get_db)):
    _require_view(request, "acordos")
    deleted_ids = select(AgreementSourceRecord.agreement_id).where(AgreementSourceRecord.source_deleted.is_(True))
    active_ids = select(AgreementSourceRecord.agreement_id).where(AgreementSourceRecord.source_deleted.is_(False))
    # Registros manuais continuam visíveis. Registros externos removidos da fonte
    # ficam preservados para auditoria, mas deixam a listagem operacional.
    stmt = select(Agreement).options(
        joinedload(Agreement.university).joinedload(University.country),
        selectinload(Agreement.actions),
    ).where(or_(Agreement.id.not_in(deleted_ids), Agreement.id.in_(active_ids)),_agreement_visible46())
    stmt = _apply_agreement_search(stmt, q)
    rows = db.scalars(stmt.order_by(Agreement.id.desc())).unique().all()
    from .agreement_archive_v53_19 import refresh_archive
    archived_sheet_ids = refresh_archive(db)
    operational_rows = [a for a in rows if a.id not in archived_sheet_ids]
    source_records = active_source_record_map(db, [a.id for a in rows])
    source = db.scalar(select(IntegrationSource).where(IntegrationSource.code == "controle_acordos_dari"))
    from .agreement_analysis_v53 import history_agreements_for_list
    historic_rows = history_agreements_for_list(db, q, rows)
    total = len(rows) + len(historic_rows)
    active_count = sum(1 for a in operational_rows if a.status == "Ativo" and a.signed_date and a.expiry_date and a.signed_date <= date.today() <= a.expiry_date)
    waiting_signature = sum(1 for a in operational_rows if normalize_progress(a.progress) == "Aguardando assinatura")
    in_progress = sum(1 for a in operational_rows if is_pending_progress(a.progress))
    soon = date.today() + timedelta(days=365)
    expiring = sum(1 for a in operational_rows if a.status == "Ativo" and a.signed_date and a.expiry_date and date.today() <= a.expiry_date <= soon)
    ctx = common_context(request, "acordos") | {
        "rows": operational_rows, "historic_rows": historic_rows, "q": q, "total": total, "active_count": active_count,
        "waiting_signature": waiting_signature, "in_progress": in_progress, "expiring": expiring,
        "source_records": source_records, "agreement_source": source,
    }
    # DARI_V53_15_EXPIRING_CARD
    from .agreement_analysis_v53 import query_agreements as _dari_query_expiring_180
    ctx["expiring"] = _dari_query_expiring_180(db, {"a_expiring_180": "1", "a_year": ""})["total"]
    return render(request, "agreements.html", ctx)

@app.get("/acordos/item/{agreement_id}", response_class=HTMLResponse)
def agreement_detail(agreement_id: int, request: Request, db: Session = Depends(get_db)):
    _require_view(request, "acordos")
    a = db.scalar(select(Agreement).options(
        joinedload(Agreement.university).joinedload(University.country),
        selectinload(Agreement.actions),
    ).where(Agreement.id == agreement_id,_agreement_visible46()))
    if not a:
        return RedirectResponse("/acordos", status_code=303)
    from .agreement_archive_v53_19 import refresh_archive, archive_history_rows
    archived_sheet_ids = refresh_archive(db)
    sheet_history = archive_history_rows(db, a.university_id)
    trace = db.scalar(select(AgreementSourceRecord).where(AgreementSourceRecord.agreement_id == a.id, AgreementSourceRecord.source_deleted.is_(False)).limit(1))
    source = db.get(IntegrationSource, trace.source_id) if trace else None
    action_ctx = _agreement_action_context(a.actions)
    return render(request, "agreement_detail.html", common_context(request, "acordos") | {
        "a": a, "archived_sheet_expired": a.id in archived_sheet_ids, "archived_sheet_history": sheet_history, "source_record": trace, "source_info": source,
        "source_managed": bool(source and source.read_only),
        "source_error": request.query_params.get("erro") == "fonte_somente_leitura",
        **action_ctx,
    })


@app.get("/acordos/item/{agreement_id}/editar", response_class=HTMLResponse)
def edit_agreement_form(agreement_id: int, request: Request, db: Session = Depends(get_db)):
    _require_edit(request, "acordos")
    a = db.scalar(select(Agreement).options(selectinload(Agreement.actions)).where(Agreement.id == agreement_id,_agreement_visible46()))
    if not a:
        return RedirectResponse("/acordos", status_code=303)
    ctx = common_context(request, "acordos") | {
        "a": a,
        "last": a.actions[-1] if a.actions else None,
        "universities": db.scalars(select(University).order_by(University.name)).all(),
    }
    ctx.update(_agreement_action_context(a.actions))
    ctx['agreement_csrf46']=_agreement_token46(request)
    from .agreement_archive_v53_19 import archive_history_rows
    ctx['archived_sheet_history']=archive_history_rows(db,a.university_id)
    return render(request, "edit_agreement.html", ctx)


@app.post("/acordos/item/{agreement_id}/editar")
def update_agreement(
    agreement_id: int, request: Request,
    university_id: int = Form(...), agreement_type: str = Form(...), sig_number: str = Form(""),
    sei_number: str = Form(""), progress: str = Form(""), ufrj_stage: str = Form(""),
    signed_date: str = Form(""), validity_years: str = Form(""), expiry_date: str = Form(""),
    status: str = Form(""), drive_folder_url: str = Form(""), ufrj_coordinator: str = Form(""), notes: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_edit(request, "acordos")
    a = db.scalar(select(Agreement).options(selectinload(Agreement.actions)).where(Agreement.id == agreement_id,_agreement_visible46()))
    if not a:
        return RedirectResponse("/acordos", status_code=303)

    before30=snapshot30(a)
    a.university_id = university_id
    a.agreement_type = agreement_type
    a.sig_number = sig_number.strip() or None
    a.sei_number = sei_number.strip() or None
    a.progress = normalize_progress(progress)
    a.ufrj_stage = ufrj_stage.strip() or None
    a.signed_date = parse_date(signed_date)
    a.validity_years = int(validity_years) if validity_years.strip().isdigit() else None
    a.expiry_date = parse_date(expiry_date)
    a.status = status.strip() or None
    a.drive_folder_url = drive_folder_url.strip() or None
    a.ufrj_coordinator = ufrj_coordinator.strip() or None
    a.notes = notes.strip() or None
    mark30(db,'agreement',a.id,request.state.user['username'],before30,snapshot30(a))
    db.commit()
    return RedirectResponse(f"/acordos/item/{a.id}", status_code=303)


def _active_agreement_trace(db: Session, agreement_id: int) -> AgreementSourceRecord | None:
    return db.scalar(
        select(AgreementSourceRecord).where(
            AgreementSourceRecord.agreement_id == agreement_id,
            AgreementSourceRecord.source_deleted.is_(False),
        ).limit(1)
    )


@app.post("/acordos/item/{agreement_id}/acoes")
def create_agreement_action(
    agreement_id: int,
    request: Request,
    next_step: str = Form(""),
    deadline_text: str = Form(""),
    action_notes: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_edit(request, "acordos")
    agreement = db.scalar(
        select(Agreement)
        .options(selectinload(Agreement.actions))
        .where(Agreement.id == agreement_id)
    )
    if not agreement:
        return RedirectResponse("/acordos", status_code=303)

    cleaned_step = next_step.strip()
    if not cleaned_step:
        return RedirectResponse(f"/acordos/item/{agreement.id}", status_code=303)

    trace = _active_agreement_trace(db, agreement.id)
    item=AgreementAction(agreement_id=agreement.id,action_source='manual',next_step=cleaned_step,deadline_text=deadline_text.strip() or None,notes=action_notes.strip() or None)
    db.add(item);db.flush()
    mark30(db,'agreement_actions',agreement.id,request.state.user['username'],{},snapshot30(item))
    db.commit()
    return RedirectResponse(f"/acordos/item/{agreement.id}",303)


@app.post("/acordos/item/{agreement_id}/acoes/{action_id}/alternar-conclusao")
def toggle_agreement_action_completion(
    agreement_id: int,
    action_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_edit(request, "acordos")
    action = db.scalar(
        select(AgreementAction).where(
            AgreementAction.id == action_id,
            AgreementAction.agreement_id == agreement_id,
        )
    )
    if not action or not (action.next_step or "").strip():
        return RedirectResponse(f"/acordos/item/{agreement_id}", status_code=303)

    before30=snapshot30(action)
    action.is_completed=not action.is_completed
    action.completed_at=datetime.utcnow() if action.is_completed else None
    mark30(db,'agreement_actions',agreement_id,request.state.user['username'],before30,snapshot30(action))
    db.commit()
    return RedirectResponse(f"/acordos/item/{agreement_id}",303)


@app.post("/acordos/item/{agreement_id}/acoes/{action_id}/editar")
def edit_agreement_action(
    agreement_id: int,
    action_id: int,
    request: Request,
    next_step: str = Form(...),
    deadline_text: str = Form(""),
    action_notes: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_edit(request, "acordos")
    action = db.scalar(
        select(AgreementAction).where(
            AgreementAction.id == action_id,
            AgreementAction.agreement_id == agreement_id,
        )
    )
    if not action:
        return RedirectResponse(f"/acordos/item/{agreement_id}", status_code=303)

    cleaned_step = next_step.strip()
    if not cleaned_step:
        return RedirectResponse(f"/acordos/item/{agreement_id}", status_code=303)

    before30=snapshot30(action)
    action.next_step=cleaned_step
    action.deadline_text=deadline_text.strip() or None
    action.notes=action_notes.strip() or None
    mark30(db,'agreement_actions',agreement_id,request.state.user['username'],before30,snapshot30(action))
    db.commit()
    return RedirectResponse(f"/acordos/item/{agreement_id}",303)


@app.post("/acordos/item/{agreement_id}/acoes/{action_id}/excluir")
def delete_agreement_action(
    agreement_id: int,
    action_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    _require_edit(request, "acordos")
    action = db.scalar(
        select(AgreementAction).where(
            AgreementAction.id == action_id,
            AgreementAction.agreement_id == agreement_id,
        )
    )
    if not action:
        return RedirectResponse(f"/acordos/item/{agreement_id}", status_code=303)

    before30=snapshot30(action)
    db.delete(action)
    mark30(db,'agreement_actions',agreement_id,request.state.user['username'],before30,{})
    db.commit()
    return RedirectResponse(f"/acordos/item/{agreement_id}",303)



def _agreement_external_source_active(db: Session) -> bool:
    source = db.scalar(
        select(IntegrationSource)
        .where(IntegrationSource.code == "controle_acordos_dari")
        .limit(1)
    )
    return bool(source and source.read_only)


@app.get("/convenios")
def old_agreements_route():
    return RedirectResponse("/acordos", status_code=307)

@app.get("/acordos/novo", response_class=HTMLResponse)
def new_agreement_form(request: Request, db: Session = Depends(get_db)):
    _require_edit(request, "acordos")
    ctx = common_context(request, "acordos") | {
        "universities": db.scalars(select(University).order_by(University.name)).all()
    }
    return render(request, "new_agreement.html", ctx)

@app.post("/acordos/novo")
def create_agreement(
    request: Request,
    university_id: int = Form(...), agreement_type: str = Form(...), sig_number: str = Form(""),
    sei_number: str = Form(""), progress: str = Form(""), ufrj_stage: str = Form(""),
    signed_date: str = Form(""), validity_years: str = Form(""), expiry_date: str = Form(""),
    status: str = Form(""), drive_folder_url: str = Form(""), ufrj_coordinator: str = Form(""), notes: str = Form(""),
    last_action: str = Form(""), next_step: str = Form(""), deadline_text: str = Form(""),
    db: Session = Depends(get_db)
):
    _require_edit(request, "acordos")
    a = Agreement(
        university_id=university_id, agreement_type=agreement_type,
        sig_number=sig_number or None, sei_number=sei_number or None,
        progress=normalize_progress(progress), ufrj_stage=ufrj_stage or None,
        signed_date=parse_date(signed_date), validity_years=int(validity_years) if validity_years.strip().isdigit() else None,
        expiry_date=parse_date(expiry_date), status=status or None,
        drive_folder_url=drive_folder_url.strip() or None,
        ufrj_coordinator=ufrj_coordinator or None, notes=notes or None,
    )
    db.add(a); db.flush()
    if last_action or next_step or deadline_text:
        db.add(AgreementAction(
            agreement_id=a.id, last_action=last_action or None, last_action_date=date.today() if last_action else None,
            next_step=next_step or None, deadline_text=deadline_text or None,
        ))
    db.commit()
    return RedirectResponse("/acordos", status_code=303)



@app.get("/{area}/documentos", response_class=HTMLResponse)
def documents_root(
    area: str, request: Request, pasta: str = "", status: str = "", erro: str = "",
    db: Session = Depends(get_db),
):
    if area not in DOCUMENT_AREAS:
        return RedirectResponse("/", status_code=303)
    _require_view(request, area)
    try:
        current = document_repo.validate_path(area, pasta)
        # Durante a transicao nao perde dados se a rede ou API central falhar.
        sync_result = synchronize_documents(db)
        rows = document_repo.list_children(db, area, current)
    except HTTPException:
        return RedirectResponse(f"/{area}/documentos", status_code=303) if pasta else RedirectResponse('/', status_code=303)

    items = []
    for entry in rows:
        rel = entry.path
        name = rel.rsplit('/', 1)[-1]
        try:
            modified = datetime.fromisoformat(entry.modified_at).strftime("%d/%m/%Y %H:%M")
        except ValueError:
            modified = "—"
        href = (f"/{area}/documentos?pasta={quote(rel, safe='')}" if entry.kind == 'folder'
                else f"/{area}/documentos/arquivo?caminho={quote(rel, safe='')}")
        items.append({"name": name, "path": rel, "kind": "folder" if entry.kind == 'folder' else "file",
                      "href": href, "modified": modified})

    breadcrumbs = []
    if current:
        cumulative = []
        for part in current.split('/'):
            cumulative.append(part)
            rel = '/'.join(cumulative)
            breadcrumbs.append({"name": part, "href": f"/{area}/documentos?pasta={quote(rel, safe='')}"})
    parent_rel = current.rsplit('/', 1)[0] if '/' in current else ''
    ctx = common_context(request, area) | {
        "area": area,
        "area_title": DOCUMENT_AREAS[area]["title"],
        "area_description": DOCUMENT_AREAS[area]["description"],
        "current_path": current,
        "current_name": current.rsplit('/', 1)[-1] if current else "Documentos",
        "parent_href": (f"/{area}/documentos?pasta={quote(parent_rel, safe='')}" if parent_rel else f"/{area}/documentos"),
        "items": items, "breadcrumbs": breadcrumbs,
        "status": status, "erro": erro,
        "document_sync": sync_result,
        "can_manage_documents": _can_edit(request, area),
    }
    return render(request, "documents.html", ctx)


@app.get("/{area}/documentos/arquivo")
def documents_open_file(area: str, request: Request, caminho: str, db: Session = Depends(get_db)):
    _require_view(request, area)
    row = document_repo.get(db, area, caminho)
    if row is None or row.kind != 'file' or row.body is None:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no banco local.")
    from io import BytesIO
    import mimetypes
    name = row.path.rsplit('/', 1)[-1]
    mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    # Sempre forcar download, evitando execucao de HTML/SVG carregado por usuario.
    return StreamingResponse(BytesIO(row.body), media_type=mime,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}",
                 "X-Content-Type-Options": "nosniff",
                 "Content-Length": str(len(row.body))})


@app.post("/{area}/documentos/pasta")
def documents_create_folder(
    area: str, request: Request, pasta_atual: str = Form(""), nome: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_edit(request, area)
    try:
        document_repo.create_folder(db, area, pasta_atual, nome)
    except HTTPException as exc:
        if exc.status_code in (404, 400): return _documents_redirect(area, erro="pasta_invalida")
        if exc.status_code == 409: return _documents_redirect(area, pasta_atual, erro="ja_existe")
        raise
    synchronize_documents(db, force=True)
    return _documents_redirect(area, pasta_atual, status="pasta_criada")


@app.post("/{area}/documentos/enviar")
def documents_upload_file(
    area: str, request: Request, pasta_atual: str = Form(""),
    arquivo: UploadFile = File(...), db: Session = Depends(get_db),
):
    _require_edit(request, area)
    try:
        # Leitura limitada para evitar esgotamento de memoria em uploads grandes.
        data = arquivo.file.read(document_repo.MAX_FILE_BYTES + 1)
        document_repo.create_file(db, area, pasta_atual,
                                  Path(arquivo.filename or 'arquivo').name, data)
    except HTTPException as exc:
        if exc.status_code == 413: return _documents_redirect(area, pasta_atual, erro="arquivo_grande")
        if exc.status_code in (400, 404): return _documents_redirect(area, pasta_atual, erro="pasta_invalida")
        raise
    finally:
        arquivo.file.close()
    synchronize_documents(db, force=True)
    return _documents_redirect(area, pasta_atual, status="arquivo_enviado")


@app.post("/{area}/documentos/renomear")
def documents_rename(
    area: str, request: Request, pasta_atual: str = Form(""),
    caminho: str = Form(...), novo_nome: str = Form(...), db: Session = Depends(get_db),
):
    _require_edit(request, area)
    try:
        document_repo.rename(db, area, caminho, novo_nome)
    except HTTPException as exc:
        return _documents_redirect(area, pasta_atual, erro="ja_existe" if exc.status_code == 409 else "item_invalido")
    synchronize_documents(db, force=True)
    return _documents_redirect(area, pasta_atual, status="renomeado")


@app.post("/{area}/documentos/excluir")
def documents_delete(
    area: str, request: Request, pasta_atual: str = Form(""), caminho: str = Form(...),
    db: Session = Depends(get_db),
):
    _require_edit(request, area)
    try:
        document_repo.delete(db, area, caminho)
    except HTTPException:
        return _documents_redirect(area, pasta_atual, erro="item_invalido")
    synchronize_documents(db, force=True)
    return _documents_redirect(area, pasta_atual, status="excluido")


@app.post("/{area}/documentos/sincronizar")
def documents_sync_now(area: str, request: Request, pasta_atual: str = Form(""),
                       db: Session = Depends(get_db)):
    _require_view(request, area)
    response = synchronize_documents(db, force=True)
    return _documents_redirect(area, pasta_atual,
        status="sincronizado" if response['state'] == 'sincronizado' else "",
        erro="central_indisponivel" if response['state'] != 'sincronizado' else "")


@app.get("/novo/{direction}", response_class=HTMLResponse)
def new_form(direction: str, request: Request, form_response: int = 0, db: Session = Depends(get_db)):
    if direction not in {"incoming","outgoing"}:
        return RedirectResponse("/")
    _require_edit(request, direction)
    form_cards = editable_form_cards48_12(db, pending_response_id=form_response or None, direction=direction)
    context=mobility_form_context(request, db, direction)
    context.update({'form_cards48_12':form_cards,'pending_form_response48_12':form_response})
    return render(request, 'new_mobility.html',context)

@app.post("/novo/{direction}")
def create_mobility(
    direction: str, request: Request,
    name: str = Form(...), email: str = Form(""), dre: str = Form(""), passport: str = Form(""),
    course_id: int = Form(...), program_id: int = Form(...), coordinator_id: int = Form(...),
    university_id: int = Form(...), semester_id: int = Form(0), new_semester: str = Form(...), mobility_type: str = Form(...),
    duration_semesters: int = Form(1), status: str = Form('Ativo'),
    pending_form_response: int = Form(0), form_field_key: list[str] = Form([]),
    form_field_value: list[str] = Form([]), db: Session = Depends(get_db)
):
    if direction not in {"incoming","outgoing"}:
        return RedirectResponse("/", status_code=303)
    _require_edit(request, direction)
    try:
        semester_id = ensure_semester29(db, valid_semester(new_semester), 0)
        if not db.get(Semester, semester_id):raise ValueError('Selecione ou informe um semestre.')
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc))
    student = Student(full_name=name, email=email or None, dre=dre or None, passport=passport or None)
    db.add(student); db.flush()
    m = Mobility(
        direction=direction, student_id=student.id, course_id=course_id, program_id=program_id,
        coordinator_id=coordinator_id, start_semester_id=semester_id, mobility_type=modality29(mobility_type),
        duration_semesters=duration_semesters, status=status,
        home_university_id=university_id if direction=="incoming" else None,
        destination_university_id=university_id if direction=="outgoing" else None,
    )
    db.add(m); db.flush()
    if direction == 'incoming': db.add(IncomingDetail(mobility_id=m.id))
    else: db.add(OutgoingDetail(mobility_id=m.id))
    save_form_cards48_12(db, m, request.state.user['username'],
        pending_form_response,form_field_key,form_field_value)
    db.commit()
    return RedirectResponse(f'/mobilidade/{m.id}' if pending_form_response else f'/{direction}',status_code=303)

@app.get("/api/dashboard")
def api_dashboard(db: Session = Depends(get_db)):
    result=[]
    from .period_filters_v53_41 import semester_options
    for sem in semester_options(db):
        if sem.id is None:
            result.append({"semester":sem.label,"incoming":0,"outgoing":0})
            continue
        result.append({
            "semester": sem.label,
            "incoming": db.scalar(select(func.count(Mobility.id)).where(Mobility.direction=="incoming", Mobility.start_semester_id==sem.id, Mobility.status != _INCOMING_RECENT_PENDING)) or 0,
            "outgoing": db.scalar(select(func.count(Mobility.id)).where(Mobility.direction=="outgoing", Mobility.start_semester_id==sem.id)) or 0,
        })
    return result


# Catalogo estatico de consultas recuperadas do Access.
# Nenhuma consulta legada e executada por esta rota.
@app.get("/consultas-historicas", response_class=HTMLResponse)
def consultas_historicas(request: Request):
    return render(request, "consultas_historicas.html",
                  common_context(request, "consultas") | {"primary_count": 36, "historical_count": 22})


# === CONSULTAS ACCESS FUNCIONAIS V2 ===
#
# Reconstrucao funcional das consultas do Access antigo.
# SOMENTE LEITURA:
# - nao executa APPEND/INSERT/UPDATE/DELETE;
# - nao modifica legado_registros;
# - nao modifica arquivos Access;
# - nao acessa Google Drive.
#
# As consultas abaixo usam legado_registros para preservar, quando possivel,
# os valores e as regras registradas na copia principal do Access.

def _acc_norm(value):
    import unicodedata
    value = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        value.encode("ascii", "ignore").decode().casefold().split()
    )


def _acc_key(value):
    if value is None or value == "":
        return ""
    try:
        n = float(value)
        if n.is_integer():
            return str(int(n))
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _acc_yes(value):
    if value is True:
        return True
    return _acc_norm(value) in {
        "sim", "yes", "true", "verdadeiro", "1", "-1"
    }


def _acc_no(value):
    if value is False:
        return True
    return _acc_norm(value) in {
        "nao", "no", "false", "falso", "0"
    }


def _acc_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _acc_sem_index(year, term):
    y = _acc_int(year)
    t = _acc_int(term)
    if y is None or t not in (1, 2):
        return None
    return y * 2 + (t - 1)


def _acc_sem_label(year, term):
    y = _acc_int(year)
    t = _acc_int(term)
    if y is None or t not in (1, 2):
        return ""
    return f"{y}/{t}"


def _acc_load_tables(db):
    import json
    from sqlalchemy import text as _text

    try:
        result = db.execute(_text("""
            SELECT tabela_origem, chave_origem, conteudo_json
            FROM legado_registros
        """)).mappings()
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Estas consultas exigem o banco historico consolidado "
                "com a tabela legado_registros."
            )
        ) from exc

    tables = {}
    for rec in result:
        tabela = rec["tabela_origem"]
        try:
            conteudo = json.loads(rec["conteudo_json"])
        except Exception:
            continue
        tables.setdefault(tabela, {})[str(rec["chave_origem"])] = conteudo
    return tables


def _acc_student_rows(tables):
    alunos = tables.get("talunos", {})
    cursos = tables.get("tcursos", {})
    universidades = tables.get("tuniversidades", {})
    projetos = tables.get("tprojetos", {})

    resultado = []

    for access_id, a in alunos.items():
        curso_id = _acc_key(a.get("aluno_curso_curso_codigo"))
        ui_id = _acc_key(a.get("aluno_universidade_intercambio"))
        uo_id = _acc_key(
            a.get("aluno_universidade_origem_universidade_codigo")
        )
        projeto_id = _acc_key(a.get("aluno_codigo_projeto"))

        curso = cursos.get(curso_id) or {}
        ui = universidades.get(ui_id) or {}
        uo = universidades.get(uo_id) or {}
        projeto = projetos.get(projeto_id) or {}

        resultado.append({
            "_access_id": access_id,
            "_raw": a,
            "_curso": curso,
            "_ui": ui,
            "_uo": uo,
            "_projeto": projeto,
            "_curso_ok": bool(curso),
            "_ui_ok": bool(ui),
            "_uo_ok": bool(uo),

            "nacionalidade": a.get("aluno_nacionalidade"),
            "dre": a.get("aluno_dre"),
            "nome": a.get("aluno_nome"),
            "passaporte": a.get("aluno_passaporte"),
            "curso": curso.get("curso_nome"),
            "email": a.get("aluno_email"),

            "universidade_intercambio": ui.get("universidade_nome"),
            "pais_intercambio":
                ui.get("universidade_pais_pais_codigo"),

            "universidade_origem": uo.get("universidade_nome"),
            "pais_origem":
                uo.get("universidade_pais_pais_codigo"),

            "ano_inicio": a.get("aluno_ano_inicio"),
            "periodo_inicio": a.get("aluno_periodo_inicio"),
            "semestre_inicio": _acc_sem_label(
                a.get("aluno_ano_inicio"),
                a.get("aluno_periodo_inicio"),
            ),

            "duracao": a.get("aluno_duracao_semestres"),
            "situacao": a.get("aluno_situacao_academica"),
            "tipo_mobilidade": a.get("aluno_tipo_mobilidade"),

            "codigo_projeto": a.get("aluno_codigo_projeto"),
            "projeto_texto":
                a.get("aluno_projeto_projeto_codigo"),
            "projeto_numero":
                projeto.get("projeto_numero")
                or projeto.get("projeto_nome")
                or a.get("aluno_projeto_projeto_codigo"),

            "contabilizar": a.get("aluno_contabilizar"),
            "pendente": a.get("aluno_pendente"),
            "retorno_calculado": a.get("aluno_retornocalculado"),
            "inicio_calculado": a.get("aluno_iniciocalculado"),

            "telefone": a.get("aluno_telefone"),
            "contato_nome": a.get("aluno_contato_nome"),
            "contato_email": a.get("aluno_contato_email"),
            "endereco": a.get("aluno_endereco"),

            "prorrogacao": a.get("aluno_prorrogacao"),
            "data_retorno": a.get("aluno_data_retorno"),
            "colacao": a.get("aluno_colacao"),
            "observacao": a.get("aluno_obsevacao"),

            "disciplinas_intercambio":
                a.get("aluno_disciplinas_intercambio"),
            "disciplinas_validadas":
                a.get("aluno_disciplinas_validadas"),
            "disciplinas_solicitadas":
                a.get("aluno_disciplinas_solicitadas"),
            "porcentagem_dispensa":
                a.get("aluno_porcentagem_dispensa"),
        })

    return resultado


def _acc_inner(row):
    return (
        row["_curso_ok"]
        and row["_ui_ok"]
        and row["_uo_ok"]
    )


def _acc_common_columns():
    return [
        ("nacionalidade", "Nacionalidade"),
        ("dre", "DRE"),
        ("nome", "Nome"),
        ("curso", "Curso"),
        ("email", "E-mail"),
        ("universidade_intercambio", "Universidade de intercâmbio"),
        ("universidade_origem", "Universidade de origem"),
        ("situacao", "Situação acadêmica antiga"),
        ("ano_inicio", "Ano"),
        ("periodo_inicio", "Semestre"),
        ("duracao", "Duração"),
        ("tipo_mobilidade", "Mobilidade"),
        ("projeto_numero", "Projeto"),
    ]


_ACCESS_QUERY_META = {
    "00-consultas-diversas": {
        "title": "00-CONSULTAS DIVERSAS",
        "description":
            "Reprodução da consulta geral da cópia principal do Access.",
        "params": [],
    },
    "alunos-em-intercambio": {
        "title": "Alunos em Intercâmbio",
        "description":
            "Situação histórica 'Em Intercâmbio' e contabilizar = Sim.",
        "params": [],
    },
    "mobilidade-ano-semestre": {
        "title": "Alunos Mobilidade Ano/Semestre",
        "description":
            "Reprodução de Alunos Intercâmbio Ano e Período.",
        "params": ["ano", "semestre"],
    },
    "previsao-retorno-brasileiros": {
        "title": "Previsão de Retorno — Brasileiros",
        "description":
            "Regra original de duração, situação e contabilização.",
        "params": ["ano", "semestre"],
    },
    "previsao-retorno-estrangeiros": {
        "title": "Previsão de Retorno — Estrangeiros",
        "description":
            "Regra original de duração, situação e contabilização.",
        "params": ["ano", "semestre"],
    },
    "duplo-diploma-ano": {
        "title": "Alunos Duplo Diploma por Ano",
        "description":
            "Preserva a regra antiga: duração maior que 2 semestres.",
        "params": ["ano"],
    },
    "brasileiros-duplo-diploma-ano": {
        "title": "Brasileiros Alunos Duplo Diploma por Ano",
        "description":
            "Brasileiros, duração maior que 2 semestres e contabilizar.",
        "params": ["ano"],
    },
    "alunos-por-projeto": {
        "title": "Alunos por Projeto",
        "description":
            "Pesquisa pelo início do nome/número do projeto, como no Access.",
        "params": ["texto"],
    },
    "mobilidade-por-universidade": {
        "title": "Mobilidade por Universidade",
        "description":
            "Procura a universidade como origem ou intercâmbio.",
        "params": ["texto"],
    },
    "mobilidade-por-pais": {
        "title": "Mobilidade por País",
        "description":
            "Consulta de mobilidade associada a país de origem ou destino.",
        "params": ["texto"],
    },
    "alunos-em-aberto": {
        "title": "Alunos em Aberto",
        "description":
            "Registros históricos com aluno_contabilizar = Não.",
        "params": [],
    },
    "situacao-pendente": {
        "title": "Alunos com Situação Pendente",
        "description":
            "Reconstrução operacional baseada na flag aluno_pendente.",
        "params": [],
    },
    "alunos-irregulares": {
        "title": "Alunos Irregulares",
        "description":
            "Reprodução da regra Cst Frm Irregulares do Access.",
        "params": [],
    },
    "universidades": {
        "title": "Universidades",
        "description":
            "Cadastro histórico completo de universidades do Access.",
        "params": ["texto"],
    },
    "lista-emails-universidades": {
        "title": "Lista de E-mails das Universidades",
        "description":
            "E-mails de coordenadores estrangeiros vinculados a alunos.",
        "params": ["candidaturas"],
    },
    "outgoing-pais": {
        "title": "Outgoing por País",
        "description":
            "Contagem de brasileiros por país de destino.",
        "params": [],
    },
    "outgoing-ano": {
        "title": "Outgoing por Ano",
        "description":
            "Contagem histórica de brasileiros por ano de início.",
        "params": [],
    },
    "incoming-pais": {
        "title": "Incoming por País",
        "description":
            "Contagem de estrangeiros por país de origem.",
        "params": [],
    },
    "incoming-ano": {
        "title": "Incoming por Ano",
        "description":
            "Contagem histórica de estrangeiros por ano de início.",
        "params": [],
    },
    "mobilidade-curso": {
        "title": "Mobilidade por Curso e Ano",
        "description":
            "Contagem por curso e nacionalidade no ano escolhido.",
        "params": ["ano"],
    },
    "dispensas": {
        "title": "Alunos / Universidade / Curso / Dispensas",
        "description":
            "Consulta-base dos antigos indicadores de dispensas.",
        "params": [],
    },
    "carta-aceite-geral": {
        "title": "Carta de Aceite — seleção GERAL",
        "description":
            "Dados da consulta que alimentava a mala direta geral.",
        "params": ["ano", "semestre"],
    },
    "carta-aceite-brafitec": {
        "title": "Carta de Aceite — seleção BRAFITEC",
        "description":
            "Dados da consulta que alimentava a mala direta BRAFITEC.",
        "params": ["ano", "semestre"],
    },
    "declaracao-matricula": {
        "title": "Declaração de Matrícula Ativa",
        "description":
            "Dados históricos que alimentavam o modelo Word.",
        "params": ["ano", "semestre"],
    },
    "declaracao-matricula-endereco": {
        "title": "Declaração de Matrícula Ativa — Passaporte e Endereço",
        "description":
            "Dados históricos que alimentavam o segundo modelo Word.",
        "params": ["ano", "semestre"],
    },
}


def _acc_apply_search(rows, busca):
    busca = _acc_norm(busca)
    if not busca:
        return rows
    resultado = []
    for row in rows:
        if any(
            busca in _acc_norm(v)
            for k, v in row.items()
            if not k.startswith("_")
        ):
            resultado.append(row)
    return resultado


def _acc_group_count(rows, key, key_label):
    counts = {}
    for r in rows:
        value = r.get(key) or "Não informado"
        counts[value] = counts.get(value, 0) + 1
    saida = [
        {key: nome, "quantidade": total}
        for nome, total in counts.items()
    ]
    saida.sort(
        key=lambda x: (-x["quantidade"], str(x[key]).casefold())
    )
    return (
        [(key, key_label), ("quantidade", "Quantidade")],
        saida,
    )


def _acc_current_semester(tables, alunos):
    atuais = []
    for sem in tables.get("semestre", {}).values():
        if _acc_yes(sem.get("semestre_atual")):
            ident = (
                sem.get("semestre_identifica??o")
                or sem.get("semestre_identificacao")
            )
            if ident:
                atuais.append(str(ident))

    def parse_label(label):
        m = re.fullmatch(r"\s*(\d{4})\s*[/.-]\s*([12])\s*", label or "")
        if not m:
            return None
        return int(m.group(1)), int(m.group(2))

    parsed = [parse_label(x) for x in atuais]
    parsed = [x for x in parsed if x]

    if parsed:
        return max(parsed)

    starts = []
    for r in alunos:
        y = _acc_int(r.get("ano_inicio"))
        s = _acc_int(r.get("periodo_inicio"))
        if y and s in (1, 2):
            starts.append((y, s))
    return max(starts) if starts else (2020, 1)


def _acc_build_query(
    db,
    slug,
    ano="",
    semestre="",
    texto="",
    candidaturas="",
    busca="",
):
    if slug not in _ACCESS_QUERY_META:
        raise HTTPException(404, "Consulta inexistente.")

    tables = _acc_load_tables(db)
    alunos = _acc_student_rows(tables)
    meta = _ACCESS_QUERY_META[slug]
    note = ""

    if slug == "00-consultas-diversas":
        columns = [
            ("nacionalidade", "Nacionalidade"),
            ("dre", "DRE"),
            ("nome", "Nome"),
            ("curso", "Curso"),
            ("universidade_intercambio", "Universidade de intercâmbio"),
            ("pais_intercambio", "País de intercâmbio"),
            ("universidade_origem", "Universidade de origem"),
            ("pais_origem", "País de origem"),
            ("ano_inicio", "Ano de início"),
            ("codigo_projeto", "Código de projeto"),
            ("projeto_texto", "Projeto (texto antigo)"),
            ("email", "E-mail"),
            ("periodo_inicio", "Semestre de início"),
            ("duracao", "Duração em semestres"),
            ("situacao", "Situação acadêmica antiga"),
            ("tipo_mobilidade", "Tipo de mobilidade"),
        ]
        rows = [r for r in alunos if _acc_inner(r)]
        note = (
            "Preserva os INNER JOINs da consulta original. "
            "É a reprodução da cópia principal do Access, "
            "não uma consulta dos dados atuais."
        )

    elif slug == "alunos-em-intercambio":
        columns = _acc_common_columns()
        rows = [
            r for r in alunos
            if _acc_inner(r)
            and _acc_norm(r["situacao"]) == "em intercambio"
            and _acc_yes(r["contabilizar"])
        ]

    elif slug == "mobilidade-ano-semestre":
        columns = _acc_common_columns()
        y = _acc_int(ano)
        s = _acc_int(semestre)
        rows = []
        if y and s in (1, 2):
            rows = [
                r for r in alunos
                if _acc_inner(r)
                and _acc_int(r["ano_inicio"]) == y
                and _acc_int(r["periodo_inicio"]) == s
                and _acc_yes(r["contabilizar"])
            ]
        else:
            note = "Informe ano e semestre para executar a consulta."

    elif slug in {
        "previsao-retorno-brasileiros",
        "previsao-retorno-estrangeiros",
    }:
        columns = _acc_common_columns() + [
            ("semestres_transcorridos", "Semestres transcorridos")
        ]
        y = _acc_int(ano)
        s = _acc_int(semestre)
        nacionalidade = (
            "brasileiro"
            if slug == "previsao-retorno-brasileiros"
            else "estrangeiro"
        )
        rows = []
        if y and s in (1, 2):
            target = _acc_sem_index(y, s)
            for r in alunos:
                start = _acc_sem_index(
                    r["ano_inicio"], r["periodo_inicio"]
                )
                dur = _acc_int(r["duracao"])
                if start is None or dur is None:
                    continue
                elapsed = target - start
                if (
                    _acc_inner(r)
                    and _acc_norm(r["nacionalidade"]) == nacionalidade
                    and _acc_norm(r["situacao"]) == "em intercambio"
                    and elapsed >= dur
                    and _acc_yes(r["contabilizar"])
                ):
                    x = dict(r)
                    x["semestres_transcorridos"] = elapsed
                    rows.append(x)
        else:
            note = "Informe o período de retorno."

    elif slug in {
        "duplo-diploma-ano",
        "brasileiros-duplo-diploma-ano",
    }:
        columns = _acc_common_columns()
        y = _acc_int(ano)
        rows = []
        if y:
            for r in alunos:
                if not (
                    _acc_inner(r)
                    and (_acc_int(r["duracao"]) or 0) > 2
                    and _acc_int(r["ano_inicio"]) == y
                    and _acc_yes(r["contabilizar"])
                ):
                    continue
                if (
                    slug == "brasileiros-duplo-diploma-ano"
                    and _acc_norm(r["nacionalidade"]) != "brasileiro"
                ):
                    continue
                rows.append(r)
        else:
            note = "Informe o ano."

    elif slug == "alunos-por-projeto":
        columns = _acc_common_columns()
        termo = _acc_norm(texto)
        rows = []
        if termo:
            rows = [
                r for r in alunos
                if _acc_inner(r)
                and _acc_yes(r["contabilizar"])
                and _acc_norm(r["projeto_numero"]).startswith(termo)
            ]
        else:
            note = "Informe o nome ou início do número do projeto."

    elif slug == "mobilidade-por-universidade":
        columns = _acc_common_columns()
        termo = _acc_norm(texto)
        rows = []
        if termo:
            rows = [
                r for r in alunos
                if _acc_inner(r)
                and (
                    termo in _acc_norm(r["universidade_intercambio"])
                    or termo in _acc_norm(r["universidade_origem"])
                    or termo == _acc_norm(r["_ui"].get(
                        "universidade_codigo_automatico"
                    ))
                    or termo == _acc_norm(r["_uo"].get(
                        "universidade_codigo_automatico"
                    ))
                )
            ]
        else:
            note = "Informe universidade ou código."

    elif slug == "mobilidade-por-pais":
        columns = _acc_common_columns()
        termo = _acc_norm(texto)
        rows = []
        if termo:
            for r in alunos:
                destino = termo in _acc_norm(r["pais_intercambio"])
                origem = termo in _acc_norm(r["pais_origem"])

                # Preserva a assimetria encontrada na SQL antiga:
                # o ramo de destino exigia contabilizar=Sim;
                # o de origem n?o aplicava a mesma condi??o.
                if (
                    (destino and _acc_yes(r["contabilizar"]))
                    or origem
                ):
                    if _acc_inner(r):
                        rows.append(r)
        else:
            note = "Informe o país."

    elif slug == "alunos-em-aberto":
        columns = _acc_common_columns()
        rows = [
            r for r in alunos
            if _acc_no(r["contabilizar"])
        ]
        note = (
            "No Access, a consulta auxiliar ainda recebia o nome selecionado "
            "no formulário. Aqui são mostrados todos os registros em aberto, "
            "com pesquisa disponível."
        )

    elif slug == "situacao-pendente":
        columns = _acc_common_columns()
        rows = [
            r for r in alunos
            if _acc_yes(r["pendente"])
        ]
        note = (
            "O botão da tela antiga foi reconstruído usando a flag "
            "aluno_pendente preservada no cadastro."
        )

    elif slug == "alunos-irregulares":
        columns = _acc_common_columns() + [
            ("retorno_calculado", "Retorno calculado"),
            ("pendente", "Pendente"),
        ]
        cy, cs = _acc_current_semester(tables, alunos)
        current_idx = _acc_sem_index(cy, cs)
        rows = []

        for r in alunos:
            if (
                _acc_norm(r["situacao"]) != "em intercambio"
                or not _acc_yes(r["contabilizar"])
                or not _acc_no(r["pendente"])
            ):
                continue

            dur = _acc_int(r["duracao"])
            nat = _acc_norm(r["nacionalidade"])

            ret = str(r["retorno_calculado"] or "")
            m = re.fullmatch(
                r"\s*(\d{4})\s*[/.-]\s*([12])\s*",
                ret
            )
            ret_idx = (
                _acc_sem_index(int(m.group(1)), int(m.group(2)))
                if m else None
            )

            if dur == 0:
                rows.append(r)
            elif ret_idx is not None:
                if nat == "brasileiro" and ret_idx <= current_idx:
                    rows.append(r)
                elif nat == "estrangeiro" and ret_idx < current_idx:
                    rows.append(r)

        note = f"Semestre marcado como atual no legado: {cy}/{cs}."

    elif slug == "universidades":
        columns = [
            ("codigo", "Código"),
            ("nome", "Universidade"),
            ("pais", "País"),
            ("acordo_geral", "Acordo geral"),
            ("inicio_acordo", "Início do acordo"),
            ("fim_acordo", "Fim do acordo"),
            ("coordenador_exterior", "Coordenador exterior"),
            ("email_exterior", "E-mail exterior"),
            ("telefone_exterior", "Telefone exterior"),
            ("endereco", "Endereço"),
            ("site", "Site"),
            ("observacao", "Observação"),
            ("contato_outgoing", "Contato Outgoing"),
            ("email_outgoing", "E-mail Outgoing"),
            ("contato_incoming", "Contato Incoming"),
            ("email_incoming", "E-mail Incoming"),
        ]
        termo = _acc_norm(texto)
        rows = []

        for codigo, u in tables.get("tuniversidades", {}).items():
            row = {
                "codigo": codigo,
                "nome": u.get("universidade_nome"),
                "pais": u.get("universidade_pais_pais_codigo"),
                "acordo_geral": u.get("universidade_acordo_geral"),
                "inicio_acordo": u.get("universidade_acordo_data_inicio"),
                "fim_acordo": u.get("universidade_acordo_data_termino"),
                "coordenador_exterior":
                    u.get("universidade_cordenador_est"),
                "email_exterior":
                    u.get("universidade_coordenador_est_email"),
                "telefone_exterior":
                    u.get("universidade_coordenador_est_telefone"),
                "endereco": u.get("universidade_endereco"),
                "site": u.get("universidade_site"),
                "observacao": u.get("universidade_observacao"),
                "contato_outgoing":
                    u.get("universidade_contato_outgoing"),
                "email_outgoing":
                    u.get("universidade_outgoing_email"),
                "contato_incoming":
                    u.get("universidade_contato_incoming"),
                "email_incoming":
                    u.get("universidade_incoming_email"),
            }
            if not termo or (
                termo in _acc_norm(row["nome"])
                or termo in _acc_norm(row["pais"])
                or termo == _acc_norm(codigo)
            ):
                rows.append(row)

    elif slug == "lista-emails-universidades":
        columns = [
            ("universidade", "Universidade"),
            ("email", "E-mail do coordenador estrangeiro"),
            ("contabilizar", "Grupo do aluno"),
        ]

        escolha = _acc_norm(candidaturas)
        # SQL antiga:
        # se resposta=Sim -> aluno_contabilizar=No
        # caso contr?rio -> aluno_contabilizar=Yes
        somente_candidaturas = escolha == "sim"
        target_value = False if somente_candidaturas else True

        vistos = set()
        rows = []

        for r in alunos:
            if not r["_ui_ok"]:
                continue

            email = r["_ui"].get(
                "universidade_coordenador_est_email"
            )
            if not email:
                continue

            if _acc_yes(r["contabilizar"]) != target_value:
                continue

            chave = (
                r["universidade_intercambio"],
                str(email),
                target_value,
            )
            if chave in vistos:
                continue
            vistos.add(chave)

            rows.append({
                "universidade":
                    r["universidade_intercambio"],
                "email": email,
                "contabilizar":
                    "Não" if somente_candidaturas else "Sim",
            })

    elif slug == "outgoing-pais":
        filtro = [
            r for r in alunos
            if _acc_inner(r)
            and _acc_norm(r["nacionalidade"]) == "brasileiro"
            and _acc_yes(r["contabilizar"])
        ]
        columns, rows = _acc_group_count(
            filtro, "pais_intercambio", "País de destino"
        )

    elif slug == "incoming-pais":
        filtro = [
            r for r in alunos
            if _acc_inner(r)
            and _acc_norm(r["nacionalidade"]) == "estrangeiro"
            and _acc_yes(r["contabilizar"])
            and _acc_norm(r["pais_origem"]) != "brasil"
        ]
        columns, rows = _acc_group_count(
            filtro, "pais_origem", "País de origem"
        )

    elif slug in {"outgoing-ano", "incoming-ano"}:
        nacionalidade = (
            "brasileiro"
            if slug == "outgoing-ano"
            else "estrangeiro"
        )
        counts = {}
        for r in alunos:
            if (
                _acc_norm(r["nacionalidade"]) == nacionalidade
                and _acc_yes(r["contabilizar"])
            ):
                y = _acc_int(r["ano_inicio"])
                if y is not None:
                    counts[y] = counts.get(y, 0) + 1
        rows = [
            {"ano": y, "quantidade": counts[y]}
            for y in sorted(counts)
        ]
        columns = [
            ("ano", "Ano"),
            ("quantidade", "Quantidade"),
        ]

    elif slug == "mobilidade-curso":
        y = _acc_int(ano)
        columns = [
            ("curso", "Curso"),
            ("nacionalidade", "Nacionalidade"),
            ("quantidade", "Quantidade"),
        ]
        rows = []
        if y:
            counts = {}
            for r in alunos:
                if _acc_int(r["ano_inicio"]) != y or not r["curso"]:
                    continue
                key = (r["curso"], r["nacionalidade"])
                counts[key] = counts.get(key, 0) + 1
            rows = [
                {
                    "curso": curso,
                    "nacionalidade": nat,
                    "quantidade": total,
                }
                for (curso, nat), total in counts.items()
            ]
            rows.sort(
                key=lambda r: (
                    str(r["curso"]).casefold(),
                    str(r["nacionalidade"]).casefold(),
                )
            )
        else:
            note = "Informe o ano."

    elif slug == "dispensas":
        columns = [
            ("curso", "Curso"),
            ("projeto_numero", "Projeto"),
            ("ano_inicio", "Ano"),
            ("periodo_inicio", "Semestre"),
            ("disciplinas_intercambio", "Disciplinas no intercâmbio"),
            ("porcentagem_dispensa", "% de dispensa"),
            ("disciplinas_validadas", "Disciplinas validadas"),
            ("disciplinas_solicitadas", "Disciplinas solicitadas"),
            ("universidade_intercambio", "Universidade"),
            ("dre", "DRE"),
            ("nome", "Nome"),
        ]
        rows = [
            r for r in alunos
            if _acc_norm(r["nacionalidade"]) == "brasileiro"
            and _acc_yes(r["contabilizar"])
            and r["porcentagem_dispensa"] not in (None, "")
        ]
        note = (
            "Esta é a consulta-base recuperada do Access. "
            "Os botões visuais '% Dispensas Concedidas' e "
            "'Número Disciplinas Concedidas' não tinham uma SQL pública "
            "isolada na cópia principal."
        )

    elif slug in {"carta-aceite-geral", "carta-aceite-brafitec"}:
        columns = [
            ("nome", "Nome"),
            ("curso", "Curso"),
            ("universidade_origem", "Universidade de origem"),
            ("pais_origem", "País"),
            ("ano_inicio", "Ano"),
            ("periodo_inicio", "Semestre"),
            ("duracao", "Duração"),
            ("inicio_calculado", "Início calculado"),
            ("retorno_calculado", "Retorno calculado"),
            ("projeto_numero", "Projeto"),
        ]
        y = _acc_int(ano)
        s = _acc_int(semestre)
        rows = []
        if y and s in (1, 2):
            want_brafitec = slug == "carta-aceite-brafitec"
            for r in alunos:
                projeto = _acc_norm(r["projeto_numero"])
                is_brafitec = projeto.startswith("brafitec")
                if (
                    _acc_norm(r["nacionalidade"]) == "estrangeiro"
                    and _acc_int(r["ano_inicio"]) == y
                    and _acc_int(r["periodo_inicio"]) == s
                    and is_brafitec == want_brafitec
                ):
                    rows.append(r)
        else:
            note = "Informe ano e semestre."

    elif slug in {
        "declaracao-matricula",
        "declaracao-matricula-endereco",
    }:
        columns = [
            ("nome", "Nome"),
            ("dre", "DRE"),
            ("curso", "Curso"),
            ("semestre_inicio", "Período de entrada"),
            ("nacionalidade", "Nacionalidade"),
        ]
        if slug == "declaracao-matricula-endereco":
            columns.insert(2, ("passaporte", "Passaporte"))
            columns.insert(3, ("endereco", "Endereço"))

        y = _acc_int(ano)
        s = _acc_int(semestre)
        rows = []

        if y and s in (1, 2):
            rows = [
                r for r in alunos
                if _acc_norm(r["nacionalidade"]) == "estrangeiro"
                and _acc_int(r["ano_inicio"]) == y
                and _acc_int(r["periodo_inicio"]) == s
                and _acc_yes(r["contabilizar"])
            ]
        else:
            note = "Informe ano e semestre."

        note = (
            (note + " " if note else "")
            + "A consulta de dados está funcional. "
              "A emissão do documento será ligada ao modelo final "
              "quando os campos atuais forem definidos."
        )

    else:
        raise HTTPException(404, "Consulta ainda não reconstruída.")

    rows = _acc_apply_search(rows, busca)

    # Ordena??o previs?vel para consultas de linhas individuais.
    if rows and "nome" in rows[0]:
        rows.sort(
            key=lambda r: _acc_norm(r.get("nome"))
        )

    return {
        "meta": meta,
        "columns": columns,
        "rows": rows,
        "note": note,
        "values": {
            "ano": ano,
            "semestre": semestre,
            "texto": texto,
            "candidaturas": candidaturas,
            "busca": busca,
        },
    }


@app.get(
    "/consultas-historicas/exportar/{slug}.csv"
)
def access_query_csv(
    slug: str,
    ano: str = "",
    semestre: str = "",
    texto: str = "",
    candidaturas: str = "",
    busca: str = "",
    db: Session = Depends(get_db),
):
    import csv
    import io

    from .admin_queries_v53_28 import legacy_destination
    destination = legacy_destination(slug, ano, semestre, busca, texto, export=True)
    if destination:
        return RedirectResponse(destination, status_code=303)

    result = _acc_build_query(
        db, slug, ano, semestre, texto, candidaturas, busca
    )

    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")

    writer.writerow(
        [label for _, label in result["columns"]]
    )

    for row in result["rows"]:
        writer.writerow(
            [row.get(key) for key, _ in result["columns"]]
        )

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{slug}.csv"'
        },
    )


@app.get(
    "/consultas-historicas/consulta/{slug}",
    response_class=HTMLResponse,
)
def access_query_view(
    slug: str,
    request: Request,
    ano: str = "",
    semestre: str = "",
    texto: str = "",
    candidaturas: str = "",
    busca: str = "",
    pagina: int = 1,
    db: Session = Depends(get_db),
):
    from urllib.parse import urlencode

    from .admin_queries_v53_28 import legacy_destination
    destination = legacy_destination(slug, ano, semestre, busca, texto, export=False)
    if destination:
        return RedirectResponse(destination, status_code=303)

    result = _acc_build_query(
        db, slug, ano, semestre, texto, candidaturas, busca
    )

    all_rows = result["rows"]
    total = len(all_rows)

    por_pagina = 50
    paginas = max(1, (total + por_pagina - 1) // por_pagina)
    pagina = max(1, min(pagina, paginas))
    inicio = (pagina - 1) * por_pagina

    values = result["values"]
    query_string = urlencode({
        k: v for k, v in values.items() if str(v or "").strip()
    })

    ctx = common_context(request, "consultas") | {
        "slug": slug,
        "meta": result["meta"],
        "columns": result["columns"],
        "rows": all_rows[inicio:inicio + por_pagina],
        "total": total,
        "pagina": pagina,
        "paginas": paginas,
        "note": result["note"],
        "values": values,
        "export_query": query_string,
    }

    return render(
        request,
        "consulta_access_funcional.html",
        ctx,
    )

# DARI v53 - Análise de Acordos (rotas independentes)
from .agreement_analysis_v53 import install_agreement_analytics_routes
install_agreement_analytics_routes(app, _require_view, render, common_context)

# DARI_V53_16_WORKFLOW_ROUTES - situacao DARI independente do status legado.
@app.post('/mobilidade/{mobility_id}/situacao')
def mobility_workflow_change(mobility_id: int, request: Request, action: str = Form(...),
                             db: Session = Depends(get_db)):
    m=db.get(Mobility,mobility_id)
    if not m or m.direction not in ('incoming','outgoing'):
        raise HTTPException(status_code=404,detail='Mobilidade nao encontrada.')
    _require_edit(request,m.direction)
    if action=='active' and m.direction=='incoming' and _incoming_recent_is_provisional(db,m.id):
        raise HTTPException(status_code=409,detail='Pre-cadastro ainda nao homologado.')
    try:
        _workflow_change_state(db,m.id,action,
            (getattr(request.state,'user',None) or {}).get('username','usuario'),force=True)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc
    return RedirectResponse(f'/mobilidade/{mobility_id}',status_code=303)

# DARI_V53_18_FICHA_ALUNO_CONCLUSAO

# DARI_V53_19_AUTO_ARCHIVE

# DARI v53.24 - Consultas administrativas atuais
from .admin_queries_v53_28 import install_routes as install_admin_queries_v53_28, safe_back
install_admin_queries_v53_28(app, _require_view, _can_edit, render, common_context, get_db, DEMO_SECRET, AUTH_COOKIE)

@app.get("/incoming/planilha/linha/{row_id}/editar", response_class=HTMLResponse)
def edit_source30(row_id:int,request:Request,db:Session=Depends(get_db)):
    _require_edit(request,"incoming")
    row=db.get(IncomingSourceRow,row_id)
    if not row:raise HTTPException(404)
    if row.mobility_id:return RedirectResponse(f"/mobilidade/{row.mobility_id}/editar",303)
    fields=source_fields30(db,row,_incoming_json(row.field_values_json,{}))
    token=hmac.new(DEMO_SECRET.encode(),("source30:"+request.cookies.get(AUTH_COOKIE,"")).encode(),hashlib.sha256).hexdigest()
    return render(request,"edit_source_v53_30.html",common_context(request,"incoming")|{"row":row,"fields":fields,"csrf":token})

@app.post("/incoming/planilha/linha/{row_id}/editar")
async def save_source30(row_id:int,request:Request,db:Session=Depends(get_db)):
    _require_edit(request,"incoming")
    row=db.get(IncomingSourceRow,row_id)
    if not row:raise HTTPException(404)
    if row.mobility_id:return RedirectResponse(f"/mobilidade/{row.mobility_id}/editar",303)
    form=await request.form()
    expected=hmac.new(DEMO_SECRET.encode(),("source30:"+request.cookies.get(AUTH_COOKIE,"")).encode(),hashlib.sha256).hexdigest()
    if not request.cookies.get(AUTH_COOKIE) or not hmac.compare_digest(str(form.get("csrf","")),expected):raise HTTPException(403)
    before=source_fields30(db,row,_incoming_json(row.field_values_json,{}))
    after=dict(before)
    for k,v in before.items():
        if not isinstance(v,(dict,list)):after[k]=str(form.get("field_"+k,v or "")).strip()
    mark30(db,"incoming_source",row.id,request.state.user["username"],before,after)
    db.commit()
    return RedirectResponse(f"/incoming/planilha/linha/{row.id}",303)


# DARI_V53_48_GOOGLE_FORMS_BRIDGE
app.include_router(form_router48)

@app.on_event('shutdown')
def stop_form_sync48():
    stop_form_poller48()
