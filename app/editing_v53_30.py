"""Edição local auditada sem alteração das fontes originais."""
import json,re,unicodedata
from sqlalchemy import text
def sort_key(v):
    v=str(v or '').strip()
    return (''.join(c for c in unicodedata.normalize('NFKD',v) if not unicodedata.combining(c)).casefold(),v.casefold())
def sorted_named(items):
    return sorted(items,key=lambda x:sort_key(x.get('name','') if isinstance(x,dict) else getattr(x,'name',x)))
def sort_context(ctx):
    for name in ('countries','courses','universities','programs','coordinators','professors','mobility_type_options','agreement_type_options','progress_options','ufrj_stage_options'):
        if isinstance(ctx.get(name),(list,tuple)):ctx[name]=sorted_named(ctx[name])
    return ctx
def mark(db,kind,ident,user,before,after):
    db.execute(text('INSERT INTO dari_local_edits_v53_30(kind,record_id,username,before_json,after_json) VALUES (:k,:id,:u,:b,:a)'),dict(k=kind,id=ident,u=user,b=json.dumps(before,ensure_ascii=False,default=str),a=json.dumps(after,ensure_ascii=False,default=str)))
def edited(db,kind,ident):
    return bool(db.execute(text('SELECT 1 FROM dari_local_edits_v53_30 WHERE kind=:k AND record_id=:id LIMIT 1'),dict(k=kind,id=ident)).scalar())
def snapshot(obj):
    return {c.name:getattr(obj,c.name) for c in obj.__table__.columns}
def source_fields(db,row,original):
    found=db.execute(text("SELECT after_json FROM dari_local_edits_v53_30 WHERE kind='incoming_source' AND record_id=:id ORDER BY id DESC LIMIT 1"),dict(id=row.id)).scalar()
    return json.loads(found) if found else original
def valid_semester(value):
    value=str(value or '').strip()
    if not re.fullmatch(r'[0-9]{4}/[12]',value):raise ValueError('Informe o semestre no formato AAAA/1 ou AAAA/2.')
    return value
