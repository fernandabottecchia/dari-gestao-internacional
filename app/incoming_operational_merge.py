from __future__ import annotations
"""Enriquecimento SOMENTE de Incoming historico, confirmado com DRE+nome+semestre.

Nunca cria Students/Mobilities, nem escolhe universidade por grafia aproximada.
Nunca sobrescreve dados existentes. Sem acesso a Google.
"""
import hashlib
import json
from collections import Counter, defaultdict

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select, inspect
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from app.database import Base
from app.models import IncomingSourceBatch, IncomingSourceRow, IncomingDetail, Mobility, Student, Semester
from scripts.reconcile_incoming_evidence import _classify, _valid_dre, _normalize_name

class IncomingFieldPromotion(Base):
    __tablename__ = 'incoming_field_promotions'
    __table_args__ = (UniqueConstraint('source_row_id','field_key',name='uq_incoming_promotion_source_field'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_row_id: Mapped[int] = mapped_column(ForeignKey('incoming_source_rows.id'), index=True)
    mobility_id: Mapped[int] = mapped_column(ForeignKey('mobilities.id'), index=True)
    field_key: Mapped[str] = mapped_column(String(100))
    source_row_sha256: Mapped[str] = mapped_column(String(64))
    value_sha256: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

# Esses campos ja pertencem ao IncomingDetail e tem semantica textual.
# Status, datas, pais, universidade, nacionalidade, curso e tempo de mobilidade
# NAO sao inferidos/alterados por esta rotina.
DETAIL_FIELDS = {
    'candidate_document': None,
    'polimail': 180,
    'coordination_notes': None,
    'intranet_access': 100,
    'identification': 100,
    'dari_form': 100,
    'study_plan': None,
    'responsible_name': 180,
    'responsible_email': 180,
}


def plan(db):
    batch=db.scalar(select(IncomingSourceBatch).order_by(IncomingSourceBatch.id.desc()).limit(1))
    if not batch:raise RuntimeError('Staging Incoming ausente; instale a v43 primeiro.')
    students=db.scalars(select(Student)).all()
    mobilities=db.scalars(select(Mobility).where(Mobility.direction=='incoming')).all()
    semesters={x.id:x.label for x in db.scalars(select(Semester)).all()}
    by_dre,by_name,by_student_mobility=defaultdict(list),defaultdict(list),defaultdict(list)
    for s in students:
        d=_valid_dre(s.dre)
        if d:by_dre[d].append(s)
        by_name[_normalize_name(s.full_name)].append(s)
    for m in mobilities:by_student_mobility[m.student_id].append(m)
    details={x.mobility_id:x for x in db.scalars(select(IncomingDetail)).all()}
    source=db.scalars(select(IncomingSourceRow).where(IncomingSourceRow.batch_id==batch.id,
             IncomingSourceRow.review_status=='linked_exact').order_by(IncomingSourceRow.id)).all()
    existing=({(p.source_row_id,p.field_key) for p in db.scalars(select(IncomingFieldPromotion)).all()}
              if inspect(db.bind).has_table('incoming_field_promotions') else set())
    proposed=[]; counts=Counter()
    for row in source:
        classification,sid,mid=_classify(row,by_dre,by_name,by_student_mobility,semesters)
        if classification!='linked_exact' or not sid or not mid or (row.student_id,row.mobility_id)!=(sid,mid):
            raise RuntimeError(f'Conflito de identidade na linha-fonte ID {row.id}; operacao interrompida.')
        detail=details.get(mid)
        if detail is None:
            counts['missing_detail']+=1
            continue
        fields=json.loads(row.field_values_json or '{}')
        for key,maxlen in DETAIL_FIELDS.items():
            value=fields.get(key)
            if value is None or str(value).strip()=='':continue
            value=str(value).strip()
            if (row.id,key) in existing:
                counts['already_promoted']+=1; continue
            if maxlen and len(value)>maxlen:
                counts['long_value_review']+=1; continue
            current=getattr(detail,key)
            if current is not None and str(current).strip():
                if str(current).strip()!=value:counts['existing_preserved']+=1
                else:counts['same_value_preserved']+=1
                continue
            proposed.append((row, detail, key, value))
            counts['eligible_fields']+=1
    counts['safe_historical_rows']=len(source)
    return proposed,counts


def apply(db):
    proposed,counts=plan(db)
    for row,detail,key,value in proposed:
        setattr(detail,key,value)
        db.add(IncomingFieldPromotion(
            source_row_id=row.id,mobility_id=detail.mobility_id,field_key=key,
            source_row_sha256=row.row_sha256,
            value_sha256=hashlib.sha256(value.encode('utf-8')).hexdigest()))
    db.flush()
    return counts
