"""Copy an authoritative SQLite snapshot into a NEW, EMPTY PostgreSQL database.

Never truncates, drops or merges existing data. DDL, inserts, validation and the
release marker run in one PostgreSQL transaction. SQLite is opened read-only.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, os, re, sqlite3, tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from sqlalchemy import (create_engine, MetaData, Table, Column, BigInteger, Integer,
    Boolean, Date, DateTime, Text, LargeBinary, Float, Numeric, Identity,
    ForeignKeyConstraint, UniqueConstraint, CheckConstraint, Index, inspect, text, select)
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateTable, CreateIndex
from sqlalchemy.dialects import postgresql

RELEASE='53.49.0-rc1'
ROOT=Path(__file__).resolve().parents[1]
REQUIRED={'students','mobilities','semesters','user_accounts','agreements',
    'dari_form_sources_v53_48','dari_mobility_schedule_v53_40','dari_deleted_mobilities_v53_48_11'}

def quote(name):return '"'+name.replace('"','""')+'"'

def readonly(path):
    path=Path(path).resolve(strict=True)
    return sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)

def snapshot(source,destination):
    with readonly(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)

def digest_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def convert(value,typ):
    if value is None:return None
    if isinstance(typ,Boolean):
        if value not in (0,1,False,True):raise ValueError('Valor não booleano em coluna BOOLEAN.')
        return bool(value)
    if isinstance(typ,DateTime):
        if isinstance(value,datetime):return value
        return datetime.fromisoformat(str(value))
    if isinstance(typ,Date):
        if isinstance(value,date):return value
        return date.fromisoformat(str(value))
    if isinstance(typ,(BigInteger,Integer)):
        if isinstance(value,float) and not value.is_integer():raise ValueError('Fração em coluna INTEGER.')
        result=int(value)
        if str(result)!=str(value) and not isinstance(value,(int,float)):raise ValueError('Texto não canônico em coluna INTEGER.')
        return result
    if isinstance(typ,LargeBinary):
        if not isinstance(value,(bytes,bytearray,memoryview)):raise ValueError('Texto em coluna BLOB.')
        return bytes(value)
    if isinstance(typ,Text):
        if not isinstance(value,str):raise ValueError('Valor não textual em coluna TEXT.')
        if '\x00' in value:raise ValueError('Texto contém NUL, incompatível com PostgreSQL.')
        return value
    return value

def canonical(value):
    if isinstance(value,(bytes,bytearray,memoryview)):return {'blob':base64.b64encode(value).decode()}
    if isinstance(value,datetime):return {'datetime':value.isoformat()}
    if isinstance(value,date):return {'date':value.isoformat()}
    return value

def row_digest(values):
    return int.from_bytes(hashlib.sha256(json.dumps([canonical(v) for v in values],ensure_ascii=False,separators=(',',':'),default=str).encode()).digest(),'big')

def audit_rows(rows):
    # Order-independent multiset digest; count + sum of row SHA256s.
    total=0;count=0
    for row in rows:total=(total+row_digest(row))%(1<<256);count+=1
    return {'rows':count,'content_digest':f'{total:064x}'}

def translated_type(typ):
    if isinstance(typ,Boolean):return Boolean()
    if isinstance(typ,DateTime):return DateTime(timezone=bool(typ.timezone))
    if isinstance(typ,Date):return Date()
    if isinstance(typ,Integer):return BigInteger()
    if isinstance(typ,LargeBinary):return LargeBinary()
    if isinstance(typ,(Float,Numeric)):return typ
    return Text() # SQLite does not enforce VARCHAR lengths; never truncate.

def build_schema(path):
    eng=create_engine('sqlite:///'+str(Path(path).resolve()))
    old=MetaData();old.reflect(bind=eng)
    new=MetaData();ins=inspect(eng)
    raw=readonly(path)
    names={t.name for t in old.tables.values() if not t.name.startswith('sqlite_')}
    if not REQUIRED<=names:raise ValueError('Snapshot incompleto ou de outra versão; faltam tabelas obrigatórias.')
    # Prevent an unexpected schema from being silently converted.
    expected=json.loads((ROOT/'deploy/source_schema.json').read_text())
    actual=dict(raw.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    objects=json.loads((ROOT/'deploy/source_objects.json').read_text())
    actual_objects=dict(raw.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('trigger','index') AND sql IS NOT NULL"))
    if actual_objects!=objects:raise ValueError('Índices ou regras SQLite diferem da versão analisada; revisar antes de migrar.')
    if actual!=expected:raise ValueError('A estrutura SQLite difere da versão analisada. Não foi feita migração; atualizar o mapeamento primeiro.')
    for source in old.tables.values():
        if source.name not in names:continue
        pk=list(source.primary_key.columns)
        columns=[]
        for c in source.columns:
            typ=translated_type(c.type)
            default=c.server_default
            if default is not None:
                val=str(default.arg)
                if isinstance(typ,Boolean):val={'0':'false','1':'true',"'0'":'false',"'1'":'true'}.get(val,val)
                default=text(val)
            args=[]
            if len(pk)==1 and pk[0].name==c.name and isinstance(typ,BigInteger):
                args=[Identity(always=False)];default=None
            columns.append(Column(c.name,typ,*args,primary_key=c.primary_key,nullable=False if c.primary_key else c.nullable,server_default=default))
        target=Table(source.name,new,*columns)
        for constraint in source.constraints:
            if isinstance(constraint,UniqueConstraint):
                target.append_constraint(UniqueConstraint(*[c.name for c in constraint.columns],name=constraint.name))
            elif isinstance(constraint,CheckConstraint):
                target.append_constraint(CheckConstraint(str(constraint.sqltext),name=constraint.name))
            elif isinstance(constraint,ForeignKeyConstraint):
                target.append_constraint(ForeignKeyConstraint([e.parent.name for e in constraint.elements],
                    [e.target_fullname for e in constraint.elements],name=constraint.name,
                    deferrable=True,initially='DEFERRED',ondelete=constraint.ondelete,onupdate=constraint.onupdate))
        for idx in ins.get_indexes(source.name):
            if any(c is None for c in idx['column_names']):raise ValueError('Índice funcional não mapeado.')
            Index(idx['name'],*[target.c[c] for c in idx['column_names']],unique=idx['unique'])
    raw.close();eng.dispose();return new

def source_rows(connection,table):
    cols=list(table.columns)
    cursor=connection.execute('SELECT '+','.join(quote(c.name) for c in cols)+' FROM '+quote(table.name))
    for row in cursor:
        try:yield tuple(convert(v,c.type) for v,c in zip(row,cols))
        except Exception as exc:raise ValueError(f'Tipo incompatível em {table.name}; a migração foi interrompida sem confirmar dados.') from exc

def validate_source(path,schema):
    with readonly(path) as src:
        if src.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('SQLite com falha de integridade.')
        if src.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('SQLite contém vínculo inválido.')
        return {t.name:audit_rows(source_rows(src,t)) for t in schema.sorted_tables}

def create_marker(conn,report):
    conn.execute(text('CREATE TABLE dari_hosting_migration (id INTEGER PRIMARY KEY, release TEXT NOT NULL, migrated_at TEXT NOT NULL, report_json TEXT NOT NULL)'))
    conn.execute(text('INSERT INTO dari_hosting_migration VALUES (1,:release,:stamp,:report)'),dict(release=RELEASE,stamp=datetime.now(timezone.utc).isoformat(),report=json.dumps(report,ensure_ascii=False)))

def migrate(path,url,report_path):
    target=make_url(url)
    if target.get_backend_name()!='postgresql':raise ValueError('Destino deve ser PostgreSQL novo e vazio.')
    if target.drivername=='postgresql':target=target.set(drivername='postgresql+psycopg')
    schema=build_schema(path);expected=validate_source(path,schema)
    report={'release':RELEASE,'sqlite_sha256':digest_file(path),'tables':expected,'counts_verified':False,'contents_verified':False}
    eng=create_engine(target,hide_parameters=True,connect_args={'connect_timeout':15})
    with readonly(path) as src,eng.begin() as conn:
        # Transaction lock and empty schema check, before any CREATE or INSERT.
        conn.execute(text('SELECT pg_advisory_xact_lock(53490001)'))
        if conn.scalar(text('SELECT current_schema()'))!='public':raise ValueError('Usar banco dedicado com schema public, vazio.')
        existing=conn.execute(text("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND c.relkind IN ('r','v','m','S','f','p')")).first()
        if existing:raise ValueError('Destino não está vazio. Nada foi apagado ou mesclado.')
        schema.create_all(conn)
        for t in schema.sorted_tables:
            batch=[]
            for values in source_rows(src,t):
                batch.append(dict(zip(t.c.keys(),values)))
                if len(batch)==500:conn.execute(t.insert(),batch);batch=[]
            if batch:conn.execute(t.insert(),batch)
            got=audit_rows(conn.execute(select(t).execution_options(stream_results=True)).yield_per(500))
            if got!=expected[t.name]:raise ValueError('Conferência de conteúdo falhou em '+t.name)
        # Existing IDs and sqlite_sequence are retained in the next identity value.
        sequence_names={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        old_sequences=dict(src.execute('SELECT name,seq FROM sqlite_sequence')) if 'sqlite_sequence' in sequence_names else {}
        for t in schema.tables.values():
            for c in t.columns:
                if c.identity:
                    peak=conn.scalar(select(__import__('sqlalchemy').func.max(c))) or 0
                    peak=max(int(peak),int(old_sequences.get(t.name,0)))
                    seq=conn.scalar(text('SELECT pg_get_serial_sequence(:tab,:col)'),dict(tab=t.name,col=c.name))
                    conn.execute(text('SELECT setval(CAST(:seq AS regclass),:n,:called)'),dict(seq=seq,n=max(peak,1),called=peak>0))
        # Install equivalent normalization triggers only AFTER exact-content validation.
        conn.exec_driver_sql((ROOT/'deploy/normalization.sql').read_text())
        conn.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        report.update(counts_verified=True,contents_verified=True)
        create_marker(conn,report)
    eng.dispose()
    Path(report_path).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sqlite',required=True);p.add_argument('--report',required=True)
    p.add_argument('--check-only',action='store_true')
    args=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='dari-migration-') as td:
        clean=Path(td)/'source.db';snapshot(args.sqlite,clean)
        if args.check_only:
            schema=build_schema(clean);r={'release':RELEASE,'tables':validate_source(clean,schema),'sqlite_sha256':digest_file(clean),'mode':'source-validation-only'}
            Path(args.report).write_text(json.dumps(r,indent=2,ensure_ascii=False))
        else:
            if not os.getenv('DATABASE_URL'):raise SystemExit('Defina DATABASE_URL para o banco novo do TIC.')
            migrate(clean,os.environ['DATABASE_URL'],args.report)
    print('Concluído. Consulte o relatório local. O SQLite original não foi alterado.')
if __name__=='__main__':main()
