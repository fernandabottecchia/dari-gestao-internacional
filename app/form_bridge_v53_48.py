"""DARI v53.48 — ponte somente-leitura entre Google Forms/Sheets e triagem.

NUNCA escreve nas planilhas. Persistência local no SQLite. As respostas são
identificadas pela fonte + identidade estável; revisões não recriam candidaturas.
A associação de Outgoing ao processo é SEMPRE confirmada pelo usuário.
"""
from __future__ import annotations

import io
import csv
import urllib.request
import urllib.error
import hashlib
import json
import mimetypes
import os
import re
import secrets
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse, quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint, func, or_, select, inspect
from sqlalchemy.orm import Session, load_only, deferred

from .database import Base, SessionLocal, get_db
from .models import Application, Mobility, Student
from .form_sources_catalog_v53_48_12 import OUTGOING_SOURCES, eligible_tab, generic_responses_tab, unique_tabs

router = APIRouter()
SCOPES = ("https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive.readonly")
MAX_FILE_BYTES = max(1, int(os.getenv("DARI_FORM_FILE_MAX_BYTES", str(100 * 1024 * 1024))))
POLL_MINUTES = max(10, int(os.getenv('DARI_FORM_POLL_MINUTES', '20')))
# Planilha Incoming fornecida pela DARI em 17/11/2026. Apenas o endereço e
# o mapeamento de colunas são incluídos no pacote; nenhuma resposta pessoal.
INCOMING_SOURCE_KEY_HASH = 'd81ccdb7dcd7b14cd515836c905ebe4c1c12f8532810fea5868c28f4874229c8'

def official_incoming(source):
    return hashlib.sha256(source.spreadsheet_id.encode()).hexdigest() == INCOMING_SOURCE_KEY_HASH
INCOMING_SOURCE_SHEET_TITLE = 'Respostas ao formulário 1'
INCOMING_IGNORE_HEADERS = frozenset({'first given name', 'last surname s'})
STOP = threading.Event()
STARTED = False
LOCK = threading.Lock()


class FormSource(Base):
    __tablename__ = 'dari_form_sources_v53_48'
    __table_args__ = (UniqueConstraint('spreadsheet_id', 'sheet_gid', name='uq_dari_form_source_sheet'),)
    id = Column(Integer, primary_key=True)
    direction = Column(String(12), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    stage = Column(String(160), nullable=False, default='')
    processing_mode = Column(String(16), nullable=False, default='review')
    spreadsheet_id = Column(String(200), nullable=False)
    sheet_gid = Column(String(24), nullable=False, default='')
    sheet_title = Column(String(200), nullable=False, default='')
    header_row = Column(Integer, nullable=False, default=1)
    active = Column(Boolean, nullable=False, default=True)
    last_checked_at = Column(String(36), nullable=True)
    last_error = Column(Text, nullable=True)
    last_new_count = Column(Integer, nullable=False, default=0)
    # Exclusao logica: mantem os documentos, respostas e vinculos ja homologados.
    archived_at = Column(String(36), nullable=True)
    # Aviso independente do estado de acesso ao Google.
    schema_warning = Column(Text, nullable=True)
    multi_tab_2026 = Column(Boolean, nullable=False, default=False)
    known_tabs_json = Column(Text, nullable=False, default='[]')


class FormResponse(Base):
    __tablename__ = 'dari_form_responses_v53_48'
    __table_args__ = (UniqueConstraint('source_id', 'source_key', name='uq_dari_form_source_key'),)
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('dari_form_sources_v53_48.id'), nullable=False, index=True)
    source_key = Column(String(64), nullable=False)
    application_id = Column(Integer, ForeignKey('applications.id'), nullable=False, unique=True, index=True)
    row_number = Column(Integer, nullable=False)
    fingerprint = Column(String(64), nullable=False)
    raw_fields_json = Column(Text, nullable=False)
    detected_keys_json = Column(Text, nullable=False)
    revision_count = Column(Integer, nullable=False, default=0)
    first_seen_at = Column(String(36), nullable=False)
    last_seen_at = Column(String(36), nullable=False)
    linked_mobility_id = Column(Integer, ForeignKey('mobilities.id'), nullable=True, index=True)
    # Respostas por identidade local estavel de coluna; raw_fields_json permanece auditavel.
    cells_json = Column(Text, nullable=True)
    source_tab = Column(String(200), nullable=True)


class FormColumn(Base):
    """Identidade interna de pergunta/coluna, independente de posicao e rotulo.

    Nao e um ID do Google Forms. Renomeacoes ambiguas nunca sao inferidas
    automaticamente apenas pela posicao na planilha.
    """
    __tablename__ = 'dari_form_columns_v53_48_7'
    __table_args__ = (UniqueConstraint('source_id', 'column_key', name='uq_dari_form_column_key'),)
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('dari_form_sources_v53_48.id'), nullable=False, index=True)
    column_key = Column(String(40), nullable=False)
    title = Column(String(500), nullable=False)
    aliases_json = Column(Text, nullable=False, default='[]')
    position = Column(Integer, nullable=False)
    visible = Column(Boolean, nullable=False, default=True)


class FormResponseRevision(Base):
    __tablename__ = 'dari_form_revisions_v53_48'
    id = Column(Integer, primary_key=True)
    response_id = Column(Integer, ForeignKey('dari_form_responses_v53_48.id'), nullable=False, index=True)
    prior_fingerprint = Column(String(64), nullable=False)
    prior_fields_json = Column(Text, nullable=False)
    recorded_at = Column(String(36), nullable=False)


class FormFile(Base):
    __tablename__ = 'dari_form_files_v53_48'
    __table_args__ = (UniqueConstraint('response_id', 'drive_file_id', name='uq_dari_form_file'),)
    id = Column(Integer, primary_key=True)
    response_id = Column(Integer, ForeignKey('dari_form_responses_v53_48.id'), nullable=False, index=True)
    drive_file_id = Column(String(180), nullable=False)
    original_url = Column(Text, nullable=False)
    name = Column(String(320), nullable=False, default='Anexo')
    mime = Column(String(180), nullable=False, default='application/octet-stream')
    size_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=True)
    body = deferred(Column(LargeBinary, nullable=True))
    error = Column(String(255), nullable=True)


Index('ix_dari_form_file_response', FormFile.response_id)


# As respostas originais permanecem imutáveis no FormResponse. As revisões
# aprovadas pela DARI ficam em tabela própria, vinculadas ao processo.
class FormApprovedExtras(Base):
    __tablename__ = 'dari_form_approved_extras_v53_48_3'
    id = Column(Integer, primary_key=True)
    response_id = Column(Integer, ForeignKey('dari_form_responses_v53_48.id'), nullable=False, unique=True, index=True)
    fields_json = Column(Text, nullable=False)
    approved_at = Column(String(36), nullable=False)


def reconcile_incoming_types48_3():
    """Corrige apenas os rótulos operacionais errados de candidaturas pendentes.

    Não reimporta nem duplica candidaturas, não altera o texto da planilha e
    não muda processos já aprovados.
    """
    with SessionLocal() as db:
        rows = db.execute(select(FormResponse, FormSource, Application)
            .join(FormSource, FormSource.id == FormResponse.source_id)
            .join(Application, Application.id == FormResponse.application_id)
            .where(FormSource.direction == 'incoming', Application.status == 'pending')).all()
        changed = False
        for response, source, application in rows:
            raw = json.loads(response.raw_fields_json or '{}')
            typ = detect(raw).get('type', '').strip()
            if not typ:
                continue
            if application.application_type != typ:
                application.application_type = typ
                changed = True
            detected = json.loads(response.detected_keys_json or '{}')
            if detected.get('type') != typ:
                detected['type'] = typ
                response.detected_keys_json = json.dumps(detected, ensure_ascii=False)
                changed = True
        if changed:
            db.commit()




def ensure_form_source_mode_schema(engine):
    """Compatibilidade com quem já instalou a v53.48 anterior."""
    if 'dari_form_sources_v53_48' not in inspect(engine).get_table_names():
        return
    fields = {c['name'] for c in inspect(engine).get_columns('dari_form_sources_v53_48')}
    alters = []
    if 'processing_mode' not in fields:
        alters.append("ALTER TABLE dari_form_sources_v53_48 ADD COLUMN processing_mode VARCHAR(16) NOT NULL DEFAULT 'review'")
    if 'archived_at' not in fields:
        alters.append("ALTER TABLE dari_form_sources_v53_48 ADD COLUMN archived_at VARCHAR(36)")
    if 'schema_warning' not in fields:
        alters.append("ALTER TABLE dari_form_sources_v53_48 ADD COLUMN schema_warning TEXT")
    if 'multi_tab_2026' not in fields:
        alters.append("ALTER TABLE dari_form_sources_v53_48 ADD COLUMN multi_tab_2026 BOOLEAN NOT NULL DEFAULT 0")
    if 'known_tabs_json' not in fields:
        alters.append("ALTER TABLE dari_form_sources_v53_48 ADD COLUMN known_tabs_json TEXT NOT NULL DEFAULT '[]'")
    if 'dari_form_responses_v53_48' in inspect(engine).get_table_names():
        response_fields = {c['name'] for c in inspect(engine).get_columns('dari_form_responses_v53_48')}
        if 'cells_json' not in response_fields:
            alters.append("ALTER TABLE dari_form_responses_v53_48 ADD COLUMN cells_json TEXT")
        if 'source_tab' not in response_fields:
            alters.append("ALTER TABLE dari_form_responses_v53_48 ADD COLUMN source_tab VARCHAR(200)")
    if alters:
        with engine.begin() as connection:
            for ddl in alters:
                connection.exec_driver_sql(ddl)



def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def norm(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()


def norm_id(value):
    return re.sub(r'\s+', '', str(value or '')).casefold()


def sheet_locator(value: str):
    raw = (value or '').strip()
    found = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]{15,200})', raw)
    if found:
        p = urlparse(raw)
        hash_args = parse_qs(p.fragment.split('?', 1)[-1])
        gid = (parse_qs(p.query).get('gid') or hash_args.get('gid') or [''])[0]
        return found.group(1), gid if gid.isdigit() else ''
    if re.fullmatch(r'[A-Za-z0-9_-]{15,200}', raw):
        return raw, ''
    raise ValueError('Cole um link válido da planilha Google Sheets (não do formulário).')


# Correspondências conservadoras: nunca criar um cadastro novo pela suposição de uma coluna.
FIELD_NAMES = {
    'submitted': ('carimbo de data hora', 'carimbo de data e hora', 'timestamp', 'data hora', 'data e hora', 'hora de envio', 'data de envio', 'data do envio'),
    'name': ('nome completo', 'nome e sobrenome', 'nome do aluno', 'nome do aluno recomendado', 'nome do estudante', 'full name', 'name and surname', 'nome'),
    'given': ('primeiro nome', 'first name', 'given name'),
    'family': ('sobrenome', 'sobrenomes', 'surname', 'last name', 'family name'),
    'email': ('endereco de email', 'e mail', 'email', 'email institucional', 'email do aluno', 'email do estudante', 'endereco de correio eletronico'),
    'dre': ('dre', 'd r e', 'numero dre', 'numero de dre', 'matricula', 'numero de matricula', 'registro academico'),
    'passport': ('numero do passaporte', 'passaporte', 'passport number'),
    'semester': ('ano semestre', 'ano letivo semestre', 'academic year semester', 'academic year semester at ufrj', 'semestre', 'semestre de intercambio', 'periodo de intercambio'),
    'course': ('curso', 'curso de origem', 'curso area de estudos', 'area de estudos', 'field of studies', 'field of study', 'field of studies this determines your main course'),
    'university': ('universidade de destino', 'universidade de origem', 'instituicao de destino', 'instituicao de origem', 'home university', 'select your home university', 'destination university'),
    'country': ('pais de origem', 'pais de destino', 'country of origin', 'destination country'),
    'type': ('tipo de mobilidade', 'modalidade', 'tipo de candidatura', 'application type', 'application information'),
    'response_id': ('response id', 'id da resposta', 'identificador da resposta', 'id resposta'),
}


def detect(fields: dict) -> dict:
    result = {}
    normalized = [(norm(k), v) for k, v in fields.items()]
    for code, choices in FIELD_NAMES.items():
        for key, value in normalized:
            if value and (key in choices or (code == 'email' and key.endswith(' email'))):
                result[code] = str(value).strip()
                break
    # Não confundir campos de e-mail de coordenador/professor com o do estudante.
    if not result.get('email'):
        for key, value in normalized:
            if value and key in {'endereco de email do aluno', 'endereco de email do estudante'}:
                result['email'] = str(value).strip()
    if not result.get('name'):
        result['name'] = ' '.join(x for x in (result.get('given', ''), result.get('family', '')) if x)
    return result


def parse_timestamp(text: str):
    if not text:
        return None
    value = str(text).strip()
    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%m/%d/%Y %H:%M:%S'):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def response_key(source_id: int, detected: dict, fields: dict, seen: dict) -> str:
    # Nunca usar número da linha como identidade primária: linhas podem ser inseridas ou movidas.
    if detected.get('response_id'):
        base = 'id|' + norm_id(detected['response_id'])
    elif detected.get('submitted'):
        # Mesmo quando a linha é editada, carimbo + identificador do respondente permanecem.
        base = 'stamp|' + norm_id(detected['submitted']) + '|' + norm_id(detected.get('dre') or detected.get('email') or detected.get('name'))
    else:
        # Sem identificador estável na origem, não há garantia contra duplicação após edição.
        base = 'payload|' + json.dumps(fields, ensure_ascii=False, sort_keys=True)
    number = seen.get(base, 0)
    seen[base] = number + 1
    if number:
        base += '|repeated:' + str(number)
    return hashlib.sha256(f'{source_id}|{base}'.encode('utf-8')).hexdigest()


def drive_ids(value: str):
    seen = set()
    ids = []
    for m in re.finditer(r'https?://(?:drive\.google\.com|docs\.google\.com)/[^\s,;<>"\']+', str(value or '')):
        url = m.group(0).rstrip(').,;')
        p = urlparse(url)
        match = re.search(r'/(?:file|document|spreadsheets|presentation)/d/([A-Za-z0-9_-]{15,180})', p.path)
        file_id = match.group(1) if match else (parse_qs(p.query).get('id') or [None])[0]
        if file_id and re.fullmatch('[A-Za-z0-9_-]{15,180}', file_id) and file_id not in seen:
            ids.append((file_id, url))
            seen.add(file_id)
    return ids


def _config_root():
    return Path(os.getenv('DARI_GOOGLE_CREDENTIALS_DIR', '/run/secrets/dari-google'))


def _google_credentials(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials
    root = _config_root()
    sa = Path(os.getenv('DARI_FORMS_SERVICE_ACCOUNT_FILE', root / 'google_forms_service_account.json'))
    if sa.is_file():
        return service_account.Credentials.from_service_account_file(str(sa), scopes=SCOPES)
    token = Path(os.getenv('DARI_FORMS_TOKEN', root / 'google_forms_readonly_token.json'))
    client = Path(os.getenv('DARI_FORMS_OAUTH_CLIENT', root / 'google_oauth_client.json'))
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding='utf-8')
    if creds and creds.valid and set(SCOPES).issubset(set(creds.scopes or [])):
        return creds
    if interactive and client.is_file():
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
        creds = flow.run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json(), encoding='utf-8')
        return creds
    raise RuntimeError('Autorização Google pendente: configure conta de serviço ou execute 03_AUTORIZAR_FORMULARIOS.cmd.')


def _google_services(interactive=False):
    from googleapiclient.discovery import build
    creds = _google_credentials(interactive)
    return (build('sheets', 'v4', credentials=creds, cache_discovery=False),
            build('drive', 'v3', credentials=creds, cache_discovery=False))


def _col_letters(index: int) -> str:
    result = ''
    index += 1
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(ord('A') + digit) + result
    return result


def _enrich_attachment_links(sheets, source: FormSource, title: str, rows: list):
    """Encontra links escondidos na formatação de células de upload do Forms.

    Alguns Sheets mostram apenas o nome do PDF, com URL dentro do hyperlink.
    Não varrer a planilha inteira como grid: consultar somente colunas candidatas.
    """
    headers_idx = source.header_row - 1
    if len(rows) <= headers_idx: return rows
    patterns = ('arquivo', 'anexo', 'upload', 'comprovante', 'documento', 'identidade',
                'passaporte', 'pdf', 'file', 'attachment', 'certificado', 'historico escolar')
    col_ids = [i for i,h in enumerate(rows[headers_idx]) if any(p in norm(h) for p in patterns)]
    if not col_ids: return rows
    stop = max(len(rows), headers_idx+1)
    ranges = [f"'{title.replace(chr(39),chr(39)*2)}'!{_col_letters(i)}{source.header_row}:{_col_letters(i)}{stop}" for i in col_ids[:30]]
    result = sheets.spreadsheets().get(spreadsheetId=source.spreadsheet_id, ranges=ranges,
        includeGridData=True,
        fields='sheets(data(startRow,startColumn,rowData(values(hyperlink,textFormatRuns(format(link(uri)))))))').execute()
    for sheet in result.get('sheets',[]):
        for block in sheet.get('data',[]):
            start_row = int(block.get('startRow', 0))
            start_col = int(block.get('startColumn', 0))
            for offset,row in enumerate(block.get('rowData',[])):
                idx = start_row+offset
                if idx < source.header_row or idx >= len(rows): continue
                for coloffset,cell in enumerate(row.get('values', [])):
                    col = start_col+coloffset
                    urls = []
                    if cell.get('hyperlink'): urls.append(cell['hyperlink'])
                    for run in cell.get('textFormatRuns',[]):
                        uri = (run.get('format',{}).get('link') or {}).get('uri')
                        if uri and uri not in urls: urls.append(uri)
                    if not urls: continue
                    while len(rows[idx]) <= col: rows[idx].append('')
                    existing = str(rows[idx][col] or '')
                    for url in urls:
                        if url not in existing: existing += ('  ' if existing else '') + url
                    rows[idx][col] = existing
    return rows


def _read_public_sheet(source: FormSource):
    """Importa uma planilha cujo acesso de leitura por link foi autorizado.

    Sem chaves/API/Google SDK. Não usa proxies nem APIs de terceiros.
    Arquivos privados e links embutidos nas células precisam de OAuth do Google.
    """
    url = 'https://docs.google.com/spreadsheets/d/' + quote(source.spreadsheet_id, safe='') + '/export?format=csv'
    effective_gid = source.sheet_gid or ('754669162' if official_incoming(source) else '')
    if effective_gid:
        url += '&gid=' + quote(effective_gid, safe='')
    gviz = 'https://docs.google.com/spreadsheets/d/' + quote(source.spreadsheet_id, safe='') + '/gviz/tq?tqx=out%3Acsv'
    if effective_gid:
        gviz += '&gid=' + quote(effective_gid, safe='')
    errors = []
    for candidate in (url, gviz):
        req = urllib.request.Request(candidate, headers={
            'User-Agent': 'Mozilla/5.0 (DARI Google Sheets read-only)', 'Accept': 'text/csv'})
        try:
            with urllib.request.urlopen(req, timeout=22) as response:
                mime = response.headers.get('Content-Type', '').lower()
                raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise ValueError('Planilha maior que 32 MB; configure acesso autenticado.')
                if 'html' in mime or raw.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                    raise PermissionError('O Google retornou uma pagina de acesso no lugar do CSV.')
                values = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
                header_idx = source.header_row - 1
                if len(values) <= header_idx or not any(str(x).strip() for x in values[header_idx]):
                    raise ValueError('A planilha retornou CSV sem cabecalho.')
                # Nao considerar paginas de erro em texto como se fossem uma planilha.
                possible_ids = set(FIELD_NAMES['submitted']) | set(FIELD_NAMES['response_id'])
                if not any(norm(x) in possible_ids for x in values[header_idx]):
                    raise ValueError('A exportacao nao retornou um cabecalho de respostas do Google Forms.')
                return {'title': source.sheet_title or 'Respostas ao formulario'}, values
        except (urllib.error.HTTPError, urllib.error.URLError, PermissionError, ValueError) as exc:
            errors.append(f'{type(exc).__name__}: {getattr(exc, "code", str(exc))}')
    raise PermissionError(
        'O Google recusou as duas formas publicas de exportacao CSV (' +
        '; '.join(errors) + '). Configure a autorizacao Google de somente leitura ' +
        'para o programa DARI; acesso no navegador nao autoriza automaticamente o Python.')


def _read_sheet(sheets, source: FormSource):
    props = sheets.spreadsheets().get(spreadsheetId=source.spreadsheet_id, fields='sheets(properties(sheetId,title))').execute()['sheets']
    if source.sheet_gid:
        selected = next((x['properties'] for x in props if str(x['properties']['sheetId']) == source.sheet_gid), None)
        if selected is None:
            raise ValueError('A aba indicada pelo gid não existe mais na planilha.')
    elif source.sheet_title:
        selected = next((x['properties'] for x in props if x['properties']['title'] == source.sheet_title), None)
        if selected is None:
            raise ValueError('A aba configurada não existe mais na planilha.')
    else:
        selected = props[0]['properties']
    title = selected['title'].replace("'", "''")
    response = sheets.spreadsheets().values().get(spreadsheetId=source.spreadsheet_id,
        range=f"'{title}'", valueRenderOption='FORMATTED_VALUE').execute()
    rows = response.get('values', [])
    try:
        rows = _enrich_attachment_links(sheets, source, selected['title'], rows)
    except Exception:
        # A indisponibilidade de hyperlinks não deve impedir a leitura das respostas.
        pass
    return selected, rows


def _row_fields(headers, cells, *, ignore_headers=()):
    fields = {}
    for col, h in enumerate(headers):
        label = str(h).strip() or f'Coluna {col+1}'
        if norm(label) in ignore_headers:
            continue
        if label in fields:
            label = f'{label} (coluna {col+1})'
        fields[label] = str(cells[col]).strip() if col < len(cells) and cells[col] is not None else ''
    return fields


def _incoming_ignore_headers(source: FormSource):
    # Nome (coluna K) é a fonte oficial para o nome completo nesta planilha.
    # As colunas I/J nunca entram na ficha da DARI nem na resposta importada.
    return INCOMING_IGNORE_HEADERS if (source.direction == 'incoming' and
        official_incoming(source)) else ()


def register_incoming_source_once():
    return False  # Configuration is migrated, never recreated at startup.


def register_outgoing_sources48_12():
    """Idempotently register supplied Google Sheets without importing older cohorts.

    The user's previous source settings, paused state, and deletion decisions
    take precedence. No responses or files are bundled or written to Google.
    """
    with SessionLocal() as db:
        changed = False
        for title, sheet_id, year_tabs, primary in OUTGOING_SOURCES:
            registered = db.scalar(select(FormSource).where(FormSource.spreadsheet_id == sheet_id).limit(1))
            if registered:
                if registered.direction == 'outgoing' and not registered.archived_at:
                    # Preserve title, pause state, and other administrator edits.
                    if not registered.multi_tab_2026:
                        registered.multi_tab_2026 = True
                        changed = True
                    known = _json_obj(registered.known_tabs_json, [])
                    merged = {str(gid):(str(name),str(gid)) for name,gid in known if isinstance(name,str)}
                    for name,gid in list(year_tabs)+[primary]:
                        merged[str(gid)]=(name,str(gid))
                    new_known = json.dumps(list(merged.values()),ensure_ascii=False)
                    if registered.known_tabs_json != new_known:
                        registered.known_tabs_json = new_known
                        changed = True
                continue
            db.add(FormSource(direction='outgoing', name=title, stage=title,
                processing_mode='review',spreadsheet_id=sheet_id,
                sheet_gid='',sheet_title='',header_row=1,active=True,
                multi_tab_2026=True,
                known_tabs_json=json.dumps(list(year_tabs)+[primary],ensure_ascii=False)))
            changed=True
        if changed:
            db.commit()
        return changed

def _save_response(db: Session, source: FormSource, row_number: int, fields: dict, detected: dict, key: str, cells: dict | None = None, existing_record=None, source_tab=None):
    now = now_iso()
    fingerprint = hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    previous = existing_record or db.scalar(select(FormResponse).where(FormResponse.source_id == source.id, FormResponse.source_key == key))
    if previous:
        previous.last_seen_at = now
        previous.row_number = row_number
        if source_tab:
            previous.source_tab = source_tab
        if cells is not None:
            # Nao excluir respostas antigas: colunas removidas ficam arquivadas.
            stored_cells = _json_obj(previous.cells_json, {})
            stored_cells.update(cells)
            previous.cells_json = json.dumps(stored_cells, ensure_ascii=False)
        if previous.fingerprint != fingerprint:
            db.add(FormResponseRevision(response_id=previous.id,
                prior_fingerprint=previous.fingerprint,
                prior_fields_json=previous.raw_fields_json, recorded_at=now))
            previous.fingerprint = fingerprint
            previous.raw_fields_json = json.dumps(fields, ensure_ascii=False)
            previous.detected_keys_json = json.dumps(detected, ensure_ascii=False)
            previous.revision_count += 1
            app = db.get(Application, previous.application_id)
            if app and app.status == 'pending':
                app.raw_payload_json = json.dumps(fields, ensure_ascii=False)
                for target, origin in [('full_name','name'),('email','email'),('dre','dre'),
                                       ('passport','passport'),('course_name','course'),
                                       ('institution_name','university'),('country_name','country'),
                                       ('academic_semester','semester'),('application_type','type')]:
                    value = detected.get(origin)
                    if value: setattr(app, target, value)
        return previous, False
    app = Application(direction=source.direction, source='forms_dari_v53_48',
        source_record_id=f'{source.id}:{key}', submitted_at=parse_timestamp(detected.get('submitted')),
        status='pending', first_name=detected.get('given') or None, surnames=detected.get('family') or None,
        full_name=detected.get('name') or None, email=detected.get('email') or None,
        dre=detected.get('dre') or None, passport=detected.get('passport') or None,
        country_name=detected.get('country') or None, academic_semester=detected.get('semester') or None,
        application_type=detected.get('type') or None,
        course_name=detected.get('course') or None,
        institution_name=detected.get('university') or None,
        raw_payload_json=json.dumps(fields, ensure_ascii=False))
    db.add(app); db.flush()
    record = FormResponse(source_id=source.id, source_key=key, application_id=app.id,
        row_number=row_number, fingerprint=fingerprint,
        raw_fields_json=json.dumps(fields, ensure_ascii=False),
        detected_keys_json=json.dumps(detected, ensure_ascii=False),
        cells_json=json.dumps(cells or {}, ensure_ascii=False),source_tab=source_tab,
        first_seen_at=now, last_seen_at=now)
    db.add(record); db.flush()
    return record, True


def _import_files(db: Session, drive, record: FormResponse, fields: dict):
    for value in fields.values():
        for file_id, url in drive_ids(str(value)):
            existing = db.scalar(select(FormFile).where(FormFile.response_id==record.id, FormFile.drive_file_id==file_id))
            if existing and existing.sha256 is not None:
                continue
            if existing is None:
                existing = FormFile(response_id=record.id, drive_file_id=file_id, original_url=url, name='Anexo')
                db.add(existing); db.flush()
            if drive is None:
                existing.error = 'Arquivo no Drive: cópia pendente de autorização Google de leitura.'
                continue
            try:
                meta = drive.files().get(fileId=file_id, fields='id,name,mimeType,size').execute()
                name = str(meta.get('name') or 'Anexo')[:320]
                mime = str(meta.get('mimeType') or 'application/octet-stream')
                declared_size = int(meta.get('size') or 0)
                if declared_size > MAX_FILE_BYTES:
                    existing.error = 'Arquivo acima do limite configurado'; continue
                stream = io.BytesIO()
                if mime.startswith('application/vnd.google-apps.'):
                    request = drive.files().export_media(fileId=file_id, mimeType='application/pdf')
                    mime = 'application/pdf'
                    if not name.endswith('.pdf'): name += '.pdf'
                else:
                    request = drive.files().get_media(fileId=file_id)
                from googleapiclient.http import MediaIoBaseDownload
                downloader = MediaIoBaseDownload(stream, request, chunksize=1024*1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk(num_retries=2)
                    if stream.tell() > MAX_FILE_BYTES:
                        raise ValueError('Arquivo excede o limite configurado')
                body = stream.getvalue()
                existing.body = body
                existing.sha256 = hashlib.sha256(body).hexdigest()
                existing.size_bytes = len(body)
                existing.name = name[:320]
                existing.mime = mime[:180]
                existing.error = None
            except Exception as exc:
                # A candidatura entra na triagem mesmo se o arquivo ainda não estiver acessível.
                existing.error = ('Download pendente: ' + str(exc))[:255]


def _json_obj(value, default):
    try:
        loaded = json.loads(value or '')
        return loaded if isinstance(loaded, type(default)) else default
    except (ValueError, TypeError):
        return default


def _column_registry(db: Session, source: FormSource, headers: list, rows: list):
    """Reconcilia colunas sem usar numero da coluna como identificador.

    Identidade exata por cabecalho unico, com fallback de correspondencia
    por valores de respostas ja reconhecidas. Inferencias inconclusivas
    viram novas perguntas e geram aviso para conferencia.
    """
    ignored = _incoming_ignore_headers(source)
    incoming = [(index, str(header).strip() or f'Coluna {index+1}')
                for index, header in enumerate(headers)
                if norm(header) not in ignored]
    previous = db.scalars(select(FormColumn).where(FormColumn.source_id == source.id)
                          .order_by(FormColumn.id)).all()
    if not previous:
        result = {}
        for index, title in incoming:
            col = FormColumn(source_id=source.id, column_key=secrets.token_hex(12),
                             title=title, position=index, visible=True)
            db.add(col)
            result[index] = col
        db.flush()
        source.schema_warning = None
        return result

    old_unique = {}
    for col in previous:
        if col.visible:
            old_unique.setdefault(norm(col.title), []).append(col)
    new_unique = {}
    for index, title in incoming:
        new_unique.setdefault(norm(title), []).append((index, title))
    assigned = {}
    consumed = set()
    for key, items in new_unique.items():
        old = old_unique.get(key, [])
        if len(items) == 1 and len(old) == 1:
            index, title = items[0]
            assigned[index] = old[0]
            consumed.add(old[0].id)
        elif len(items) > 1 and len(old) == len(items):
            # Cabecalhos repetidos nao sao identificaveis apenas pelo texto.
            # Mantem indices se nao mudaram; novos movimentos ficam pendentes.
            old_by_index = {x.position:x for x in old}
            if all(index in old_by_index for index, _ in items):
                for index, _ in items:
                    assigned[index] = old_by_index[index]
                    consumed.add(old_by_index[index].id)

    remaining_old = [c for c in previous if c.visible and c.id not in consumed]
    remaining_new = [(i,t) for i,t in incoming if i not in assigned]
    # Valores identificam algumas renomeacoes reais mesmo apos reordenacao.
    # Um titulo trocado, porem sem amostras comparaveis, NAO autoriza
    # inferencia arriscada sobre dados pessoais.
    old_values = {c.id:{} for c in remaining_old}
    if remaining_old and remaining_new:
        historical = db.scalars(select(FormResponse)
                              .where(FormResponse.source_id == source.id)).all()
        old_by_key = {r.source_key:r for r in historical}
        timestamps = {}
        for r in historical:
            stamp = norm_id(_json_obj(r.detected_keys_json, {}).get('submitted'))
            if stamp:
                timestamps.setdefault(stamp, []).append(r)
        seen = {}
        for row in rows:
            if not any(str(v).strip() for v in row):
                continue
            fields = _row_fields(headers, row, ignore_headers=ignored)
            detected = detect(fields)
            key = response_key(source.id, detected, fields, seen)
            historic = old_by_key.get(key)
            if historic is None:
                stamp = norm_id(detected.get('submitted'))
                choices = timestamps.get(stamp, []) if stamp else []
                historic = choices[0] if len(choices) == 1 else None
            if historic is None:
                continue
            original = _json_obj(historic.raw_fields_json, {})
            cell_data = _json_obj(historic.cells_json, {})
            for old in remaining_old:
                prior = cell_data.get(old.column_key)
                if prior is None:
                    for alias in [old.title] + _json_obj(old.aliases_json, []):
                        if alias in original:
                            prior = original[alias]
                            break
                if prior not in (None, ''):
                    old_values[old.id][key] = str(prior).strip()
        # Rodadas curtas com planilhas pequenas: uma coluna nova pode ter
        # multiplos nomes similares; exige evidencia em >=2 respostas.
        candidates = {}
        seen = {}
        for row in rows:
            if not any(str(v).strip() for v in row): continue
            fields = _row_fields(headers, row, ignore_headers=ignored)
            key = response_key(source.id, detect(fields), fields, seen)
            for index, _ in remaining_new:
                val = str(row[index]).strip() if index < len(row) else ''
                if val:
                    candidates.setdefault(index, {})[key] = val
        scores = []
        for old in remaining_old:
            prior = old_values.get(old.id, {})
            for index, _ in remaining_new:
                fresh = candidates.get(index, {})
                common = set(prior).intersection(fresh)
                if len(common) < 2:
                    continue
                same = sum(prior[key] == fresh[key] for key in common)
                if same >= 2 and same / len(common) >= 0.95:
                    scores.append((same, old, index))
        scores.sort(key=lambda item: item[0], reverse=True)
        for score, old, index in scores:
            if index in assigned or old.id in consumed: continue
            competitors = [(s, c, i) for s,c,i in scores
                           if (i == index and c.id != old.id) or (c.id == old.id and i != index)]
            if competitors and competitors[0][0] >= score:
                continue
            assigned[index] = old
            consumed.add(old.id)

    changed = []
    for index, title in incoming:
        col = assigned.get(index)
        if col is None:
            col = FormColumn(source_id=source.id, column_key=secrets.token_hex(12),
                             title=title, position=index, visible=True)
            db.add(col)
            assigned[index] = col
        else:
            if title != col.title:
                aliases = _json_obj(col.aliases_json, [])
                if col.title not in aliases:
                    aliases.append(col.title)
                col.aliases_json = json.dumps(aliases, ensure_ascii=False)
                changed.append(f'{col.title} → {title}')
            col.title = title
            col.position = index
            col.visible = True
    for col in previous:
        if col.id not in consumed:
            col.visible = False
    unresolved_old = [c for c in remaining_old if c.id not in consumed]
    unresolved_new = [(i,t) for i,t in remaining_new if i not in assigned or assigned[i].id not in consumed]
    if unresolved_old and unresolved_new:
        source.schema_warning = ('Mudanca de perguntas possivelmente ambigua: '
            'nao foi possivel provar quais colunas foram renomeadas. '
            'Confira os cards; os dados antigos continuam guardados no historico.')
    else:
        source.schema_warning = None
    db.flush()
    return assigned


def _response_cells(headers, cells, column_registry: dict) -> dict:
    return {col.column_key: (str(cells[index]).strip() if index < len(cells) and cells[index] is not None else '')
            for index, col in column_registry.items()}



def _multitab_snapshots(sheets, source):
    from types import SimpleNamespace
    known = _json_obj(source.known_tabs_json, [])
    discovered = []
    if sheets is not None:
        try:
            meta = sheets.spreadsheets().get(spreadsheetId=source.spreadsheet_id,
                 fields='sheets(properties(sheetId,title))').execute()
            discovered = [(p['title'],str(p['sheetId'])) for row in meta.get('sheets',[])
                          for p in [row.get('properties',{})] if p.get('title') and p.get('sheetId') is not None]
        except Exception:
            # Keep the known 2026 tabs and report failures if none can be read.
            pass
    tabs = unique_tabs(known,discovered)
    if not tabs:
        raise ValueError('Nenhuma aba de 2026 em diante identificada. Confira as abas da planilha e a autorização Google.')
    snapshots=[]
    errors=[]
    for title,gid,is_generic in tabs:
        holder=SimpleNamespace(spreadsheet_id=source.spreadsheet_id,
            sheet_gid=gid,sheet_title=title,header_row=source.header_row)
        try:
            if sheets is not None:
                try:
                    props,values=_read_sheet(sheets,holder)
                except Exception:
                    props,values=_read_public_sheet(holder)
            else:
                props,values=_read_public_sheet(holder)
            if len(values) <= source.header_row-1:
                continue
            headers=values[source.header_row-1]
            ignored = _incoming_ignore_headers(source)
            selected=[]
            for offset,row in enumerate(values[source.header_row:], start=source.header_row+1):
                if not any(str(cell or '').strip() for cell in row):continue
                parsed=detect(_row_fields(headers,row,ignore_headers=ignored))
                stamp=parse_timestamp(parsed.get('submitted'))
                # Never import records from 2025 or earlier in a generic tab.
                # A dated semester tab is authoritative for its cohort, even
                # if the student originally submitted during the previous year.
                if is_generic and not (stamp and stamp.year >= 2026):continue
                if not (parsed.get('submitted') or parsed.get('response_id')):
                    # Skip administrative rows, commentary and totals instead of
                    # failing the entire workbook import.
                    continue
                selected.append((offset,row))
            snapshots.append((title,headers,selected))
        except Exception as exc:
            errors.append(f'{title}: {type(exc).__name__}: {str(exc)[:180]}')
    if not snapshots:
        raise PermissionError('Não foi possível ler nenhuma aba elegível: '+'; '.join(errors[:5]))
    return snapshots, errors

def _union_snapshot_rows(snapshots):
    """One question registry for all tabs, independent of original column order."""
    headers=[]; indices={}; aligned=[]
    for tab,heads,rows in snapshots:
        order={}; column_map=[]
        for cell in heads:
            title=str(cell or '').strip() or 'Coluna sem título'
            normalized=norm(title)
            order[normalized]=order.get(normalized,0)+1
            stable=(normalized,order[normalized])
            if stable not in indices:
                indices[stable]=len(headers)
                # Differentiate repeated question titles without silently
                # merging their answers (e.g. 1ª/2ª Opção).
                label=title if order[normalized]==1 else f'{title} (ocorrência {order[normalized]})'
                headers.append(label)
            column_map.append(indices[stable])
        for position,row in rows:
            values=['']*len(headers)
            for src,dst in enumerate(column_map):
                if src<len(row):values[dst]=row[src]
            aligned.append((tab,position,values))
        # Later tabs may introduce fields; pad earlier rows after union finishes.
    count=len(headers)
    return headers,[(t,i,r+['']*(count-len(r))) for t,i,r in aligned]

def sync_source(sheets, drive, source_id: int):
    with SessionLocal() as db:
        source=db.get(FormSource,source_id)
        if not source or not source.active or source.archived_at:return 0
        try:
            warning=[]
            if source.multi_tab_2026:
                snapshots,warning=_multitab_snapshots(sheets,source)
                headers,records=_union_snapshot_rows(snapshots)
                registry=_column_registry(db,source,headers,[r for _,_,r in records])
                entries=[(title,row_number,headers,row) for title,row_number,row in records]
            else:
                if sheets is None:
                    props,values=_read_public_sheet(source)
                else:
                    try:props,values=_read_sheet(sheets,source)
                    except Exception:props,values=_read_public_sheet(source)
                if len(values)<=source.header_row-1:raise ValueError('Cabeçalho não encontrado na aba.')
                headers=values[source.header_row-1]
                registry=_column_registry(db,source,headers,values[source.header_row:])
                entries=[(props['title'],i,headers,row) for i,row in enumerate(values[source.header_row:],start=source.header_row+1)
                         if any(str(v or '').strip() for v in row)]
            db.refresh(source)
            if not source.active or source.archived_at:return 0
            ignored=_incoming_ignore_headers(source)
            previous_records=db.scalars(select(FormResponse).where(FormResponse.source_id==source.id)).all()
            by_key={r.source_key:r for r in previous_records}
            by_stamp={}
            for r in previous_records:
                stamp=norm_id(_json_obj(r.detected_keys_json,{}).get('submitted'))
                if stamp:by_stamp.setdefault(stamp,[]).append(r)
            used_keys=set(); inserted=0; seen_by_tab={}
            for title,row_number,heads,row in entries:
                fields=_row_fields(heads,row,ignore_headers=ignored)
                detected=detect(fields)
                if not (detected.get('submitted') or detected.get('response_id')):
                    raise ValueError('Fonte sem Carimbo de data/hora ou ID de resposta: confira os títulos das colunas.')
                # Each tab can contain a copy of the same response. Do not
                # promote copied rows into duplicate applications.
                seen=seen_by_tab.setdefault(title,{})
                key=response_key(source.id,detected,fields,seen)
                if key in used_keys:continue
                used_keys.add(key)
                existing=by_key.get(key)
                stamp=norm_id(detected.get('submitted'))
                if existing is None and stamp:
                    options=by_stamp.get(stamp,[])
                    if len(options)==1:existing=options[0]
                    elif len(options)>1:
                        raise ValueError('Mesmo carimbo de data/hora em mais de uma resposta; confira a identificação da fonte.')
                result,is_new=_save_response(db,source,row_number,fields,detected,key,
                    cells=_response_cells(heads,row,registry),existing_record=existing,source_tab=title)
                if is_new:
                    by_key[key]=result
                    if stamp:by_stamp.setdefault(stamp,[]).append(result)
                attach={k:v for k,v in fields.items() if norm(k)=='file upload'} if ignored else fields
                _import_files(db,drive,result,attach)
                if is_new:inserted+=1
                db.commit()
            source.last_checked_at=now_iso();source.last_new_count=inserted
            # A partially unavailable sheet must not be shown as fully healthy.
            source.last_error=('Algumas abas não puderam ser lidas: '+'; '.join(warning[:6])) if warning else None
            db.commit()
            return inserted
        except Exception as exc:
            db.rollback();source=db.get(FormSource,source_id)
            source.last_error=(type(exc).__name__+': '+str(exc))[:1500]
            source.last_checked_at=now_iso();db.commit()
            return 0

def sync_all():
    if not LOCK.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            ids = [x[0] for x in db.execute(select(FormSource.id).where(FormSource.active.is_(True), FormSource.archived_at.is_(None))).all()]
        if not ids:
            return
        try:
            sheets, drive = _google_services()
        except Exception:
            # Planilhas com acesso de leitura por link não precisam de SDK,
            # credenciais, créditos ou configuração fora da própria interface.
            sheets, drive = None, None
        for ident in ids:
            if STOP.is_set():
                break
            sync_source(sheets, drive, ident)
    finally:
        LOCK.release()


def _poll_loop():
    if not STOP.wait(8):
        sync_all()
    while not STOP.wait(POLL_MINUTES*60):
        sync_all()


def start_poller():
    global STARTED
    if STARTED or os.getenv('DARI_FORMS_AUTOSYNC_DISABLED', '1')=='1': return
    STARTED=True
    threading.Thread(target=_poll_loop, name='DARI-Google-Forms-Leitura', daemon=True).start()


def stop_poller():
    STOP.set()


def _load_mapped_record(db: Session, application_id: int):
    return db.scalar(select(FormResponse).where(FormResponse.application_id == application_id))


def application_extras(db: Session, application_ids):
    ids = list(application_ids)
    if not ids: return {}
    rows = db.execute(select(FormResponse, FormSource).join(FormSource, FormSource.id==FormResponse.source_id)
                      .where(FormResponse.application_id.in_(ids))).all()
    response_ids = [row.id for row, src in rows]
    files_by = {}
    if response_ids:
        for file in db.scalars(select(FormFile).options(load_only(FormFile.id, FormFile.response_id, FormFile.drive_file_id, FormFile.original_url, FormFile.name, FormFile.mime, FormFile.size_bytes, FormFile.sha256, FormFile.error)).where(FormFile.response_id.in_(response_ids))).all():
            files_by.setdefault(file.response_id, []).append(file)
    return {row.application_id: {'response':row, 'source':src, 'files':files_by.get(row.id, [])}
            for row, src in rows}


def mobility_extras(db: Session, mobility_id: int):
    rows = db.execute(select(FormResponse, FormSource, Application)
        .join(FormSource, FormSource.id == FormResponse.source_id)
        .join(Application, Application.id == FormResponse.application_id)
        .where(or_(FormResponse.linked_mobility_id == mobility_id, Application.mobility_id == mobility_id))
        .order_by(FormResponse.id)).all()
    output = []
    for resp, source, app in rows:
        fields = _json_obj(resp.raw_fields_json, {})
        approved = db.scalar(select(FormApprovedExtras).where(FormApprovedExtras.response_id == resp.id))
        edited = _json_obj(approved.fields_json, {}) if approved else {}
        columns = db.scalars(select(FormColumn).where(FormColumn.source_id == source.id)
                             .order_by(FormColumn.position, FormColumn.id)).all()
        stored_cells = _json_obj(resp.cells_json, {})
        if columns and stored_cells:
            shown = []
            for col in columns:
                # Fonte excluida permanece com seu ultimo conjunto de cards;
                # uma pergunta removida da fonte ativa nao aparece no card.
                if not col.visible: continue
                value = stored_cells.get(col.column_key)
                if value is None:
                    # Compatibilidade com respostas anteriores a v53.48.7.
                    value = fields.get(col.title)
                aliases = [col.title] + _json_obj(col.aliases_json, [])
                if col.column_key in edited:
                    value = edited[col.column_key]
                else:
                    for alias in aliases:
                        if alias in edited:
                            value = edited[alias]
                            break
                if value is not None and str(value).strip():
                    shown.append((str(col.title), str(value)))
        else:
            # Fonte ja excluida antes de primeira leitura com registro de colunas:
            # preservar dados legados tal como foram homologados.
            fields.update({k:v for k,v in edited.items() if k in fields})
            shown = [(str(k),str(v)) for k,v in fields.items() if str(v).strip()]
        files = db.scalars(select(FormFile).options(load_only(FormFile.id, FormFile.response_id, FormFile.name, FormFile.sha256, FormFile.mime, FormFile.error)).where(FormFile.response_id == resp.id)).all()
        output.append({'response':resp, 'source':source, 'application':app,
                       'fields':shown, 'files':files})
    return output


def candidate_matches(db: Session, record: FormResponse, query: str = ''):
    """Pesquisa em todos os campos relacionais da ficha Outgoing.

    Campos de Student, Mobility, OutgoingDetail, Universidade, Pais,
    Curso, Programa, Coordenador e Semestre. Nunca assume um vinculo:
    a selecao e confirmada explicitamente pelo usuario.
    """
    from sqlalchemy import cast, String, inspect as sql_inspect
    from .models import Course, Program, Coordinator, University, Country, Semester, OutgoingDetail, IncomingDetail
    from sqlalchemy.orm import joinedload
    from sqlalchemy.sql.sqltypes import LargeBinary

    app = db.get(Application, record.application_id)
    direction = app.direction if app else 'outgoing'
    university_field = (Mobility.home_university_id if direction == 'incoming' else Mobility.destination_university_id)
    stmt = (select(Mobility)
        .join(Student, Mobility.student_id == Student.id)
        .outerjoin(Course, Mobility.course_id == Course.id)
        .outerjoin(Program, Mobility.program_id == Program.id)
        .outerjoin(Coordinator, Mobility.coordinator_id == Coordinator.id)
        .outerjoin(University, university_field == University.id)
        .outerjoin(Country, University.country_id == Country.id)
        .outerjoin(Semester, Mobility.start_semester_id == Semester.id)
        .outerjoin(OutgoingDetail, OutgoingDetail.mobility_id == Mobility.id)
        .outerjoin(IncomingDetail, IncomingDetail.mobility_id == Mobility.id)
        .options(joinedload(Mobility.student), joinedload(Mobility.destination_university),
                 joinedload(Mobility.course), joinedload(Mobility.start_semester))
        .where(Mobility.direction == direction))
    needle = (query or '').strip()[:120]
    if needle:
        # Escape de curingas LIKE para pesquisas literais e previsiveis.
        escaped = needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pat = '%' + escaped + '%'
        clauses = [cast(column, String).ilike(pat, escape='\\')
                   for model in (Student, Mobility, IncomingDetail, OutgoingDetail, Course, Program,
                                 Coordinator, University, Country, Semester)
                   for column in sql_inspect(model).columns
                   if not isinstance(column.type, LargeBinary)]
        # Tambem encontra valores dos cards de formularios ja vinculados.
        existing_form_matches = select(FormResponse.linked_mobility_id).where(
            FormResponse.raw_fields_json.ilike(pat, escape='\\'),
            FormResponse.linked_mobility_id.is_not(None))
        clauses.append(Mobility.id.in_(existing_form_matches))
        stmt = stmt.where(or_(*clauses))
    else:
        # Na abertura, sugere identidades correspondentes sem vincular por conta propria.
        possible = []
        if app and app.dre: possible.append(func.lower(Student.dre) == app.dre.strip().lower())
        if app and app.email: possible.append(func.lower(Student.email) == app.email.strip().lower())
        if app and app.full_name: possible.append(func.lower(Student.full_name) == app.full_name.strip().lower())
        if not possible:
            return []
        stmt = stmt.where(or_(*possible))
    return db.scalars(stmt.order_by(Mobility.id.desc()).limit(50)).unique().all()


def _require(request, direction: str, edit=False):
    from .main import _require_edit, _require_view
    return (_require_edit if edit else _require_view)(request, direction)


def _admin(request):
    from .main import _require_admin
    _require_admin(request)


def _render(request, template, context, active):
    from .main import render, common_context
    return render(request, template, common_context(request, active) | context)


@router.get('/candidaturas/fontes', response_class=HTMLResponse)
def sources_page(request: Request, direcao: str='incoming', db: Session=Depends(get_db)):
    _admin(request)
    if direcao not in ('incoming', 'outgoing'): direcao='incoming'
    sources = db.scalars(select(FormSource).where(FormSource.direction==direcao, FormSource.archived_at.is_(None)).order_by(FormSource.id)).all()
    from datetime import timedelta
    brt = timezone(timedelta(hours=-3))
    local_times = {}
    for source in sources:
        if source.last_checked_at:
            try:
                dt = datetime.fromisoformat(source.last_checked_at.replace('Z', '+00:00'))
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                local_times[source.id] = dt.astimezone(brt).strftime('%d/%m/%Y %H:%M:%S BRT')
            except (ValueError, TypeError):
                local_times[source.id] = 'Horário indisponível'
    outgoing_titles = (
        'Formulário de Registro de Saída',
        'Formulário de Registro de Chegada',
        'Formulário de Alteração de Plano de Estudos',
        'Formulário de Prorrogação do Intercâmbio',
        'Formulário de Registro de Retorno',
        'Formulário para Carta de Recomendação',
        'CAPES - Formulário de Cadastro de Bolsistas BRAFITEC',
        'Formulário de Inscrição para Seleção de Candidatos',
    )
    from .form_bridge_v53_48 import norm
    configured = {norm(source.name) for source in sources}
    waiting = [title for title in outgoing_titles if norm(title) not in configured] if direcao == 'outgoing' else []
    return _render(request, 'form_sources_v53_48.html', {
        'sources48':sources, 'direction48':direcao, 'last_checked_brt48':local_times,
        'outgoing_waiting48':waiting,
        'max_sources48':{'incoming':1,'outgoing':8}}, direcao)


@router.post('/candidaturas/fontes')
def add_source(request: Request, direction:str=Form(...), name:str=Form(...),
               url:str=Form(...), stage:str=Form(''), processing_mode:str=Form('review'), sheet_title:str=Form(''),
               header_row:int=Form(1), db:Session=Depends(get_db)):
    _admin(request)
    if direction not in ('incoming','outgoing'): raise HTTPException(400,'Fluxo inválido.')
    if not name.strip() or len(name)>200 or len(stage)>160 or len(sheet_title)>200:
        raise HTTPException(400,'Nome, etapa ou aba inválidos.')
    if processing_mode not in ('new','supplement','review'):
        raise HTTPException(400,'Finalidade inválida.')
    processing_mode = 'new' if direction == 'incoming' else 'review'
    if header_row not in range(1,21): raise HTTPException(400,'Linha do cabeçalho deve ser de 1 a 20.')
    try: spreadsheet_id,gid = sheet_locator(url)
    except ValueError as exc: raise HTTPException(400,str(exc))
    exists = db.scalar(select(FormSource).where(FormSource.spreadsheet_id==spreadsheet_id, FormSource.sheet_gid==gid))
    if exists:
        if not exists.archived_at:
            raise HTTPException(409,'Esta planilha e aba já estão configuradas.')
        # Reconectar a mesma fonte reusa as identidades existentes, sem duplicar.
        if exists.direction != direction:
            raise HTTPException(409,'Esta planilha foi usada em outro fluxo. Procure o administrador.')
        exists.archived_at = None
        exists.active = True
        exists.name = name.strip()
        exists.stage = stage.strip()
        exists.sheet_title = sheet_title.strip()
        exists.header_row = header_row
        exists.processing_mode = processing_mode
    else:
        db.add(FormSource(direction=direction,name=name.strip(),stage=stage.strip(),processing_mode=processing_mode,sheet_title=sheet_title.strip(),
                      spreadsheet_id=spreadsheet_id,sheet_gid=gid,header_row=header_row,active=True))
    db.commit()
    # Não exige clique para atualizar respostas após configurar uma nova fonte.
    if os.getenv('DARI_FORMS_AUTOSYNC_DISABLED', '1') != '1':
        threading.Thread(target=sync_all, name='DARI-Forms-Nova-Fonte', daemon=True).start()
    return RedirectResponse(f'/candidaturas/fontes?direcao={direction}',303)


@router.post('/candidaturas/fontes/{source_id}/editar')
def edit_source(source_id: int, request: Request, name: str=Form(...), stage: str=Form(''),
                processing_mode: str=Form('review'), url: str=Form(''), sheet_title: str=Form(''),
                header_row: int=Form(1), db: Session=Depends(get_db)):
    _admin(request)
    source=db.get(FormSource, source_id)
    if not source or source.archived_at: raise HTTPException(404, 'Formulário não encontrado.')
    if not name.strip() or len(name)>200 or len(stage)>160:
        raise HTTPException(400,'Nome ou etapa inválidos.')
    if processing_mode not in ('new','supplement','review'):
        raise HTTPException(400,'Finalidade inválida.')
    if header_row not in range(1, 21) or len(sheet_title) > 200:
        raise HTTPException(400,'Aba ou cabeçalho inválido.')
    if url.strip():
        try: spreadsheet_id, gid = sheet_locator(url)
        except ValueError as exc: raise HTTPException(400, str(exc))
        if (spreadsheet_id, gid) != (source.spreadsheet_id, source.sheet_gid):
            has_responses = db.scalar(select(FormResponse.id).where(FormResponse.source_id == source.id).limit(1))
            if has_responses:
                raise HTTPException(409, 'Esta fonte já possui respostas importadas. Para preservar a origem e evitar duplicações, cadastre um novo link como uma nova fonte.')
            duplicate = db.scalar(select(FormSource.id).where(FormSource.spreadsheet_id == spreadsheet_id,
                FormSource.sheet_gid == gid, FormSource.id != source.id).limit(1))
            if duplicate: raise HTTPException(409, 'Esta planilha já está cadastrada.')
            source.spreadsheet_id, source.sheet_gid = spreadsheet_id, gid
            source.last_checked_at, source.last_error = None, None
    source.sheet_title = sheet_title.strip()
    source.header_row = header_row
    source.name=name.strip()
    source.stage=stage.strip()
    source.processing_mode='new' if source.direction=='incoming' else 'review'
    db.commit()
    return RedirectResponse(f'/candidaturas/fontes?direcao={source.direction}',303)


@router.post('/candidaturas/fontes/{source_id}/alternar')
def toggle_source(source_id:int,request:Request,db:Session=Depends(get_db)):
    _admin(request)
    source=db.get(FormSource,source_id)
    if not source or source.archived_at: raise HTTPException(404)
    source.active=not source.active
    db.commit()
    return RedirectResponse(f'/candidaturas/fontes?direcao={source.direction}',303)


@router.post('/candidaturas/fontes/{source_id}/excluir')
def archive_source(source_id: int, request: Request, db: Session = Depends(get_db)):
    """Remove a fonte da configuracao sem excluir dados pessoais importados.

    Preserva FormResponse, revisoes, arquivos, candidaturas, mobilidades,
    extras aprovados e o ultimo esquema de perguntas. Nao altera Google.
    """
    _admin(request)
    source = db.get(FormSource, source_id)
    if not source or source.archived_at:
        raise HTTPException(404, 'Formulário não encontrado.')
    source.active = False
    source.archived_at = now_iso()
    db.commit()
    return RedirectResponse(f'/candidaturas/fontes?direcao={source.direction}', 303)


@router.get('/candidaturas/respostas/{response_id}/vincular', response_class=HTMLResponse)
def match_page(response_id:int,request:Request,q:str='',db:Session=Depends(get_db)):
    record=db.get(FormResponse,response_id)
    if not record: raise HTTPException(404)
    app=db.get(Application,record.application_id)
    _require(request,app.direction,True)
    if app.direction not in ('incoming','outgoing'): raise HTTPException(404)
    source=db.get(FormSource,record.source_id)
    choices=candidate_matches(db,record,q)
    return _render(request,'form_match_v53_48.html',{'app48':app,'form_response48':record,
        'form_source48':source,'matches48':choices,'q48':q},app.direction)


@router.post('/candidaturas/respostas/{response_id}/vincular')
def match_confirm(response_id:int,request:Request,mobility_id:int=Form(...),db:Session=Depends(get_db)):
    """Selection is navigation only. No database writes until process Save."""
    record=db.get(FormResponse,response_id)
    if not record:raise HTTPException(404)
    app=db.get(Application,record.application_id)
    if not app:raise HTTPException(404)
    _require(request,app.direction,True)
    if app.status!='pending' or record.linked_mobility_id:
        raise HTTPException(409,'Resposta já foi utilizada.')
    mobility=db.get(Mobility,mobility_id)
    if not mobility or mobility.direction!=app.direction:
        raise HTTPException(400,'Processo de outro fluxo ou inexistente.')
    return RedirectResponse(f'/mobilidade/{mobility.id}/editar?form_response={record.id}',303)


@router.get('/candidaturas/respostas/{response_id}/anexos/{file_id}')
def file_download(response_id:int,file_id:int,request:Request,db:Session=Depends(get_db)):
    response=db.get(FormResponse,response_id)
    if not response: raise HTTPException(404)
    source=db.get(FormSource,response.source_id)
    _require(request,source.direction)
    file=db.get(FormFile,file_id)
    if not file or file.response_id != response_id or file.body is None:
        raise HTTPException(404,'Anexo não disponível na cópia local.')
    safe_name=re.sub(r'[\r\n"\\/]', '_', file.name)[:180]
    return StreamingResponse(iter([file.body]),media_type=file.mime or 'application/octet-stream',
        headers={'Content-Disposition':f"attachment; filename*=UTF-8''{quote(safe_name)}"})


def authorize_cli():
    credentials=_google_credentials(interactive=True)
    print('Autorização Google somente leitura concluída.', 'Scopes:', ', '.join(credentials.scopes or SCOPES))


if __name__=='__main__':
    authorize_cli()

# DARI v53.48.12 — cards editáveis, confirmação somente no Salvar do processo.
def editable_form_cards48_12(db: Session, mobility_id=None, pending_response_id=None, direction=None):
    """Read-only editor context; opening a response never links it to a process."""
    if mobility_id is not None:
        response_ids = [r.id for r in db.scalars(select(FormResponse).join(
            Application, Application.id == FormResponse.application_id).where(or_(
            FormResponse.linked_mobility_id == mobility_id, Application.mobility_id == mobility_id
        ))).all()]
    else:
        response_ids = []
    if pending_response_id:
        response = db.get(FormResponse, pending_response_id)
        app = db.get(Application, response.application_id) if response else None
        if not response or not app or app.status != 'pending' or response.linked_mobility_id:
            raise HTTPException(409, 'Resposta indisponível para associação. Atualize a triagem.')
        if direction and app.direction != direction:
            raise HTTPException(403, 'Resposta de outro fluxo.')
        if pending_response_id not in response_ids:
            response_ids.append(pending_response_id)
    cards = []
    for response_id in response_ids:
        response=db.get(FormResponse,response_id)
        app=db.get(Application,response.application_id)
        source=db.get(FormSource,response.source_id)
        if direction and app.direction != direction:
            raise HTTPException(403, 'Formulário de outro fluxo.')
        stored=_json_obj(response.cells_json,{})
        raw=_json_obj(response.raw_fields_json,{})
        approved=db.scalar(select(FormApprovedExtras).where(FormApprovedExtras.response_id==response.id))
        edited=_json_obj(approved.fields_json,{}) if approved else {}
        cols=db.scalars(select(FormColumn).where(FormColumn.source_id==source.id,
            FormColumn.visible.is_(True)).order_by(FormColumn.position,FormColumn.id)).all()
        fields=[]
        if cols:
            for col in cols:
                value=stored.get(col.column_key)
                if value is None:
                    value=next((raw[k] for k in [col.title]+_json_obj(col.aliases_json,[]) if k in raw),'')
                edited_val=edited.get(col.column_key)
                if edited_val is None:
                    edited_val=next((edited[k] for k in [col.title]+_json_obj(col.aliases_json,[]) if k in edited),None)
                if edited_val is not None:value=edited_val
                fields.append({'key':f'{response.id}:{col.column_key}', 'label':col.title, 'value':str(value or '')})
        else:
            # Legacy answers imported before the per-question registry.
            for title,value in raw.items():
                marker='legacy_'+hashlib.sha256(str(title).encode()).hexdigest()[:24]
                fields.append({'key':f'{response.id}:{marker}', 'label':str(title),
                    'value':str(edited.get(marker,edited.get(title,value)) or '')})
        cards.append({'response_id':response.id, 'source_name':source.name,
            'tab':response.source_tab or source.sheet_title, 'stage':source.stage,
            'pending':response.id==pending_response_id, 'fields':fields})
    return cards


def save_form_cards48_12(db: Session, mobility: Mobility, user: str,
                         pending_response_id: int, keys: list, values: list):
    """Called *before the same db.commit()* as the process data.

    Atomic: an incomplete/forged card is rejected; no association happens
    until the surrounding process creation/edit transaction succeeds.
    """
    if len(keys)!=len(values) or len(set(keys))!=len(keys) or len(keys)>1200:
        raise HTTPException(422,'Campos do formulário inválidos.')
    if sum(len(str(v)) for v in values)>600_000:
        raise HTTPException(422,'Respostas acima do limite permitido.')
    cards=editable_form_cards48_12(db,mobility_id=mobility.id,
        pending_response_id=pending_response_id or None,direction=mobility.direction)
    expected={field['key'] for card in cards for field in card['fields']}
    if expected != set(keys):
        raise HTTPException(409,'O formulário mudou ou a página foi atualizada. Abra a ficha novamente.')
    values_by=dict(zip(keys,[str(v) for v in values]))
    for card in cards:
        resp=db.get(FormResponse,card['response_id'])
        raw_values={field['key'].split(':',1)[1]:values_by[field['key']]
                    for field in card['fields']}
        approved=db.scalar(select(FormApprovedExtras).where(FormApprovedExtras.response_id==resp.id))
        if approved is None:
            approved=FormApprovedExtras(response_id=resp.id,fields_json='{}',approved_at=now_iso())
            db.add(approved)
        approved.fields_json=json.dumps(raw_values,ensure_ascii=False)
        approved.approved_at=now_iso()
        if card['pending']:
            app=db.get(Application,resp.application_id)
            if app.status!='pending' or resp.linked_mobility_id:
                raise HTTPException(409,'A resposta já foi utilizada por outro processo.')
            resp.linked_mobility_id=mobility.id
            app.mobility_id=mobility.id
            app.status='approved'
            app.reviewed_at=datetime.now()
            app.reviewed_by=user
