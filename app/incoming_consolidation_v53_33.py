"""Leitura da conciliação; não altera registros durante consultas."""
import json
from sqlalchemy import inspect, text, select
from .models import Country

def profiles(db):
    if not inspect(db.connection()).has_table('incoming_profile_v53_33'):return {}
    return {r['mobility_id']:dict(r) for r in db.execute(text('SELECT * FROM incoming_profile_v53_33')).mappings()}

def attach(db,rows):
    data=profiles(db);countries={x.id:x for x in db.scalars(select(Country))}
    for m in rows:
        if m.direction!='incoming':continue
        p=data.get(m.id)
        m._incoming_profile33=p
        # País informado para a pessoa e país da instituição são conceitos distintos.
        # Preservar a fonte evita converter Escócia em Reino Unido, por exemplo.
        m._incoming_country33=(countries.get((p or {}).get('country_id')) or (m.home_university.country if m.home_university else None))
    from .outgoing_consolidation_v53_38 import attach as outgoing_attach
    outgoing_attach(db,rows)
    return data

def country(m):
    if m.direction=='incoming' and hasattr(m,'_incoming_country33'):return m._incoming_country33
    if m.direction=='outgoing' and hasattr(m,'_outgoing_country38'):return m._outgoing_country38
    u=m.home_university if m.direction=='incoming' else m.destination_university
    return u.country if u else None

def detail(db,m):
    data=attach(db,[m]);p=data.get(m.id)
    if not p:return {}
    sources=[]
    for row in db.execute(text('SELECT * FROM incoming_sources_v53_33 WHERE mobility_id=:mid ORDER BY period DESC,sheet,source_row'),{'mid':m.id}).mappings():
        d=dict(row);d['fields']=json.loads(d.pop('fields_json'));sources.append(d)
    legacy=db.execute(text('SELECT * FROM legado_atributos_uteis WHERE mobility_id=:mid'),{'mid':m.id}).mappings().first()
    labels={'telefone':'Telefone','curso_nome_historico':'Curso no Access','codigo_legado':'Código no Access','data_retorno_historica':'Retorno histórico','situacao_academica_historica':'Situação acadêmica histórica','observacao_access':'Observações do Access','tipo_mobilidade_historico':'Modalidade no Access','prorrogacao_historica':'Prorrogação histórica'}
    legacy_fields={label:legacy[k] for k,label in labels.items() if legacy and legacy[k]}
    previous=[dict(r) for r in db.execute(text('SELECT id,sheet_name,source_row FROM incoming_source_rows WHERE mobility_id=:mid ORDER BY sheet_name DESC,source_row'),{'mid':m.id}).mappings()]
    annotations=[dict(r) for r in db.execute(text('SELECT sheet,cell,comment,hyperlink FROM incoming_annotations_v53_33 WHERE mobility_id=:mid'),{'mid':m.id}).mappings()]
    from .mobility_periods_v53_29 import presence,activity
    periods=presence(db,[m])
    return {'incoming_annotations33':annotations,'incoming_consolidation':p,'incoming_sources33':sources,'incoming_legacy33':legacy_fields,'incoming_previous33':previous,'incoming_periods33':sorted(periods[m.id]),'incoming_active33':m.id in activity(db,[m],periods=periods)}
