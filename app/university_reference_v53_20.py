"""Padronização conservadora de instituições pela referência Acordos vigentes.
A referência contém APENAS Universidade e Sigla; nenhum dado de acordo é usado.
Não une IDs, não altera chaves estrangeiras, nem sobrescreve fontes brutas.
"""
from __future__ import annotations
import collections
import difflib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def clean(value: Any) -> str:
    return ' '.join(str(value or '').replace('\t', ' ').strip().split())


def key(value: Any) -> str:
    s = unicodedata.normalize('NFKD', clean(value).casefold())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', s).split())


def split_suffix(name: str) -> tuple[str,str]:
    """Detecta abreviação explícita após o último separador (não inventa siglas)."""
    match = re.search(r'\s[-–—]\s([A-Za-zÀ-ÿ0-9]{2,15}(?:[ -][A-Za-z0-9]{1,9})?)$',name)
    if match and len(match.group(1)) <= 20:
        return name[:match.start()].strip(), match.group(1)
    return name, ''


def display(name:str,acronym:str) -> str:
    name,acronym=clean(name),clean(acronym)
    if not acronym:return name
    # Evitar duplicar uma sigla que já faz parte do próprio nome de referência.
    if key(name).endswith(' '+key(acronym)) and re.search(r'(?:\s[-–—]\s|\()'+re.escape(acronym)+r'\)?$',name,re.I):
        return name
    return name+' - '+acronym


def source_load(json_path: str|Path):
    payload=json.loads(Path(json_path).read_text(encoding='utf8'))
    assert payload['used_columns']==['Universidade','Sigla']
    return payload


def make_index(ref):
    names={key(r['name']):r for r in ref['valid_names']}
    blocked={x['name_key'] for x in ref['conflicting_same_name']}
    acronyms=collections.defaultdict(set)
    for namekey,r in names.items():
        ak=key(r['acronym'])
        if ak:acronyms[ak].add(namekey)
    return names,blocked,acronyms


def reference_match(name: str, acronym: str|None, index):
    names,blocked,byacro=index
    name=clean(name);acronym=clean(acronym)
    n=key(name)
    base,suffix=split_suffix(name)
    b=key(base)
    if n in blocked or b in blocked:
        return None
    candidates={}
    for k in (n,b):
        if k in names:
            candidates[k]=(names[k],1 if k==n else .995,'nome idêntico'+(' sem sufixo' if k!=n else ''))
    suffix_acro=key(acronym or suffix)
    # Nunca usar sigla isolada; a grafia do nome também precisa coincidir quase integralmente.
    if suffix_acro:
        for k in byacro.get(suffix_acro,set()):
            score=max(difflib.SequenceMatcher(None,n,k).ratio(),difflib.SequenceMatcher(None,b,k).ratio())
            if score >= .94:
                candidates[k]=(names[k],score,'sigla e grafia compatíveis')
    # Erros tipográficos mínimos, sem inferir sucessões, faculdades ou campi.
    for k,r in names.items():
        if k in candidates:continue
        # Poda conservadora: similaridade >=96,5% exige início e comprimento próximos.
        # Evita comparar todas as 379 referências contra todos os cadastros.
        if not b or k[:2] != b[:2] or abs(len(k)-len(b)) > max(4,int(len(b)*.08)):
            continue
        score=max(difflib.SequenceMatcher(None,n,k).ratio(),difflib.SequenceMatcher(None,b,k).ratio())
        if score>=.965 and len(k)>15 and len(b)>15:
            candidates[k]=(r,score,'variação ortográfica mínima')
    if not candidates:return None
    ranked=sorted(candidates.values(), key=lambda item:(item[1],bool(item[0]['acronym'])),reverse=True)
    if len(ranked)>1 and ranked[0][1]-ranked[1][1]<.07 and key(ranked[0][0]['name'])!=key(ranked[1][0]['name']):
        return None
    r,score,why=ranked[0]
    oldacro=key(acronym or suffix)
    newacro=key(r['acronym'])
    if oldacro and newacro and not (oldacro==newacro or oldacro.startswith(newacro+' ')):
        return None
    # Somente nome+sufixo presente na referência, sem descartar sigla histórica
    # quando a própria referência deixa Sigla vazia.
    new_acro=r['acronym'] or acronym or suffix
    if not r['acronym'] and suffix and clean(name).endswith(suffix):
        new_name=name
    else:
        new_name=display(r['name'],new_acro)
    return {'new_name':new_name,'new_acronym':new_acro or None,
            'reference_name':r['name'],'reference_acronym':r['acronym'],
            'source_lines':r['csv_lines'],'reason':why,'score':round(score,4)}


def _table_exists(conn,name):
    return conn.execute('SELECT 1 FROM sqlite_master WHERE type="table" AND name=?',(name,)).fetchone() is not None


def create_tables(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS dari_university_name_map_v53_20 (
      university_id INTEGER PRIMARY KEY, previous_name TEXT NOT NULL, previous_acronym TEXT,
      reference_name TEXT NOT NULL, reference_acronym TEXT, applied_name TEXT NOT NULL,
      csv_lines_json TEXT NOT NULL, method TEXT NOT NULL, applied_at TEXT NOT NULL,
      FOREIGN KEY(university_id) REFERENCES universities(id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS dari_university_alias_v53_20 (
      alias_key TEXT PRIMARY KEY, university_id INTEGER NOT NULL,
      canonical_name TEXT NOT NULL, source_label TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(university_id) REFERENCES universities(id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS dari_university_review_v53_20 (
      university_id INTEGER PRIMARY KEY, source_name TEXT NOT NULL, reason TEXT NOT NULL,
      FOREIGN KEY(university_id) REFERENCES universities(id))''')


def plan(conn, ref):
    index=make_index(ref)
    rows=conn.execute('SELECT id,name,acronym FROM universities ORDER BY id').fetchall()
    matched=[];unmatched=[]
    for uid,name,acronym in rows:
        result=reference_match(name,acronym,index)
        if result:matched.append(dict(university_id=uid,previous_name=name,previous_acronym=acronym,**result))
        else:unmatched.append((uid,name,acronym))
    # Universities.name é UNIQUE: IDs diferentes nunca podem receber literalmente
    # o mesmo nome. Preservamos um registro canônico e encaminhamos os outros
    # para análise de identidade, SEM fundir IDs nem seus vínculos.
    targets=collections.defaultdict(list)
    for m in matched:targets[m['new_name']].append(m)
    used_names={name:uid for uid,name,_ in rows}
    denied=set()
    for target,group in targets.items():
        if len(group)>1:
            own=next((m for m in group if m['previous_name']==target),None)
            chosen=(own or min(group,key=lambda m:m['university_id']))
            for m in group:
                if m['university_id']!=chosen['university_id']:
                    denied.add(m['university_id'])
        # Unmatched names may already occupy the canonical spelling.
        owner=used_names.get(target)
        if owner is not None and owner not in {m['university_id'] for m in group}:
            for m in group:denied.add(m['university_id'])
    if denied:
        keep=[]
        for m in matched:
            if m['university_id'] in denied:
                unmatched.append((m['university_id'],m['previous_name'],m['previous_acronym']))
            else:keep.append(m)
        matched=keep
    return matched,unmatched


def apply(conn:sqlite3.Connection, ref:dict,*, dry_run=False):
    """Executar dentro de uma transação do instalador. Nunca cria instituições.
    A tabela de aliases evita que formulários antigos gerem uma nova instituição.
    """
    matched,unmatched=plan(conn,ref)
    stats=collections.Counter()
    stats['matched_universities']=len(matched)
    stats['unmatched_universities']=len(unmatched)
    if dry_run:return dict(stats),matched,unmatched
    create_tables(conn)
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    # Um ID mantém sua identidade; apenas name/acronym do cadastro principal são alterados.
    for m in matched:
        uid=m['university_id']
        if clean(m['previous_name'])!=m['new_name'] or clean(m['previous_acronym'])!=clean(m['new_acronym']):
            conn.execute('UPDATE universities SET name=?,acronym=? WHERE id=?',
                         (m['new_name'],m['new_acronym'],uid))
            stats['university_rows_updated']+=1
            conn.execute('''INSERT OR IGNORE INTO dari_university_name_map_v53_20
                (university_id,previous_name,previous_acronym,reference_name,reference_acronym,
                 applied_name,csv_lines_json,method,applied_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                (uid,m['previous_name'],m['previous_acronym'],m['reference_name'],
                 m['reference_acronym'],m['new_name'],json.dumps(m['source_lines']),m['reason'],now))
    # Aliases seguros só quando o texto identifica UM ÚNICO ID. Duplicatas não são fundidas.
    aliases=collections.defaultdict(set)
    labels={r['university_id']:r['new_name'] for r in matched}
    for m in matched:
        uid=m['university_id']; old=m['previous_name'];base,_=split_suffix(old)
        for label in (old,base,m['reference_name'],m['new_name']):
            if key(label): aliases[key(label)].add(uid)
    literal_names=collections.defaultdict(set)
    for existing_id,existing_name in conn.execute('SELECT id,name FROM universities'):
        literal_names[key(existing_name)].add(existing_id)
    for alias_key,ids in sorted(aliases.items()):
        if len(ids)!=1:continue
        uid=next(iter(ids))
        # Um nome ainda utilizado por outro ID não pode ser alias de um terceiro.
        if literal_names.get(alias_key,set()) - {uid}:continue
        # Não remover alias preexistente, nem substituí-lo caso se refira a outro ID.
        existing=conn.execute('SELECT university_id FROM dari_university_alias_v53_20 WHERE alias_key=?',(alias_key,)).fetchone()
        if existing and existing[0]!=uid:continue
        conn.execute('''INSERT OR IGNORE INTO dari_university_alias_v53_20
          (alias_key,university_id,canonical_name,source_label,created_at) VALUES (?,?,?,?,?)''',
          (alias_key,uid,labels[uid],ref['reference_source'],now))
        if not existing:stats['aliases_inserted']+=1
    # Actualizar apenas rótulos derivados de IDs já homologados. Nunca modificar source_value.
    for table,idcol,namecol in (
        ('incoming_v51_university_aliases','canonical_university_id','canonical_name'),
        ('incoming_v48_review_approvals','university_id_proposal','approved_university_name'),
        ('incoming_v49_operational_corrections','new_home_university_id','approved_university_name')):
        if not _table_exists(conn,table):continue
        for uid,label in labels.items():
            cur=conn.execute(f'UPDATE "{table}" SET "{namecol}"=? WHERE "{idcol}"=? AND COALESCE("{namecol}",\'\')<>?',(label,uid,label))
            stats['linked_labels_updated']+=cur.rowcount
    # Não homologar as pendências de Incoming. Sugestões são registradas sem alterar
    # confidence/existing_university_id nem a revisão humana de 155 variantes.
    if _table_exists(conn,'incoming_university_normalization'):
        for src, in conn.execute('SELECT source_value FROM incoming_university_normalization').fetchall():
            ids=aliases.get(key(src),set())
            if len(ids)==1:stats['pending_sources_with_safe_reference']+=1
    return dict(stats),matched,unmatched


def resolve_alias_orm(db,name:str):
    """Retorna University apenas se há alias inequívoco; sem inferência por sigla vaga."""
    if not name:return None
    try:
        from sqlalchemy import text
        from sqlalchemy import inspect
        uid = None
        if inspect(db.connection()).has_table('dari_university_alias_v53_22'):
            uid = db.execute(text('SELECT university_id FROM dari_university_alias_v53_22 WHERE alias_key=:k'), {'k':key(name)}).scalar_one_or_none()
        if uid is None and inspect(db.connection()).has_table('dari_university_alias_v53_20'):
            uid=db.execute(text('SELECT university_id FROM dari_university_alias_v53_20 WHERE alias_key=:k'),{'k':key(name)}).scalar_one_or_none()
        if uid is None:return None
        from .models import University
        return db.get(University,int(uid))
    except Exception as exc:
        # Bases anteriores à instalação ainda não possuem a tabela.
        if 'no such table: dari_university_alias_v53_20' in str(exc):return None
        raise
