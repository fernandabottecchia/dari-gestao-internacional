"""Histórico institucional local do DARI (v52).

Relacionamentos aprovados não substituem o nome da instituição original da
mobilidade. O filtro de uma instituição sucessora inclui seus predecessores;
escolas de uma mesma rede sem sucessão não são fundidas.

Leituras compatíveis com bases ainda sem migração v52; nenhum acesso ao Drive.
"""
from __future__ import annotations

from sqlalchemy import text, inspect
from sqlalchemy.orm import Session


def _has(db: Session, table: str) -> bool:
    return inspect(db.get_bind()).has_table(table)


def predecessors(db: Session, university_id: int) -> set[int]:
    """Fecho transitivo de predecessores; permite fusões e renomeações em cadeia."""
    result = {int(university_id)}
    # Same name/country may have historical IDs; keep all original FKs.
    same = db.execute(text('SELECT id FROM universities WHERE name = (SELECT name FROM universities WHERE id=:id) AND (country_id = (SELECT country_id FROM universities WHERE id=:id) OR (country_id IS NULL AND (SELECT country_id FROM universities WHERE id=:id) IS NULL))'), {'id':university_id}).all()
    result.update(int(row[0]) for row in same)
    if _has(db, 'dari_university_parent_v53_22'):
        edges = db.execute(text('SELECT child_id,parent_id FROM dari_university_parent_v53_22')).all()
        changed = True
        while changed:
            before = len(result)
            result.update(child for child,parent in edges if parent in result)
            changed = len(result) != before
    if not _has(db, 'university_successions'):
        return result
    edges = db.execute(text(
        'SELECT predecessor_id, successor_id FROM university_successions'
    )).all()
    changed = True
    while changed:
        before = len(result)
        result.update(old for old, new in edges if new in result)
        changed = len(result) != before
    return result


def matching_universities(db: Session, query: str) -> set[int]:
    """Busca nome, sigla e aliases; expande SOMENTE predecessores confirmados."""
    term = (query or '').strip().casefold()
    if not term:
        return set()
    found = {int(id_) for id_, name, acronym in db.execute(text(
        'SELECT id, name, acronym FROM universities'
    )).all() if term in (name or '').casefold() or term in (acronym or '').casefold()}
    if _has(db, 'university_name_aliases'):
        for alias, uid in db.execute(text(
            'SELECT alias, university_id FROM university_name_aliases'
        )):
            if term in alias.casefold():
                found.add(int(uid))
    if _has(db, 'incoming_v51_university_aliases'):
        for alias, uid in db.execute(text(
            'SELECT source_value, canonical_university_id FROM incoming_v51_university_aliases'
        )):
            if term in alias.casefold():
                found.add(int(uid))
    if _has(db, 'dari_university_alias_v53_22'):
        from .university_reference_v53_20 import key
        normalized_term = key(query)
        for alias, uid in db.execute(text('SELECT alias_key,university_id FROM dari_university_alias_v53_22')):
            if normalized_term and normalized_term in alias:
                found.add(int(uid))
    # Amplia dos sucessores já correspondentes para o histórico comprovado;
    # não retorna sucessores ao pesquisar exclusivamente o nome de predecessores.
    result: set[int] = set()
    for uid in found:
        result.update(predecessors(db, uid))
    return result


def matching_source_labels(db: Session, university_ids: set[int]) -> set[str]:
    """Inclui registros provisórios cujo FK ainda não foi homologado."""
    if not university_ids:
        return set()
    labels: set[str] = set()
    if _has(db, 'incoming_university_normalization'):
        labels.update(name for name, uid in db.execute(text(
            'SELECT source_value, existing_university_id FROM incoming_university_normalization '
            'WHERE existing_university_id IS NOT NULL'
        )) if uid in university_ids)
    if _has(db, 'incoming_v51_university_aliases'):
        labels.update(name for name, uid in db.execute(text(
            'SELECT source_value, canonical_university_id FROM incoming_v51_university_aliases'
        )) if uid in university_ids)
    if _has(db, 'university_name_aliases'):
        labels.update(name for name, uid in db.execute(text(
            'SELECT alias, university_id FROM university_name_aliases'
        )) if uid in university_ids)
    labels.update(name for uid, name in db.execute(text('SELECT id,name FROM universities'))
                  if uid in university_ids)
    return labels


def ultimate_successors(db: Session) -> dict[int, int]:
    """Mapa de agrupamento estatístico, só quando a sucessão é inequívoca."""
    if not _has(db, 'university_successions'):
        return {}
    from collections import defaultdict
    edges = defaultdict(set)
    for previous, current in db.execute(text(
            'SELECT predecessor_id,successor_id FROM university_successions')):
        edges[previous].add(current)
    result = {}
    for first in edges:
        current = first
        visited = {first}
        while len(edges.get(current, set())) == 1:
            next_ = next(iter(edges[current]))
            if next_ in visited:
                break
            current = next_
            visited.add(current)
        if current != first and not edges.get(current):
            result[first] = current
    return result
