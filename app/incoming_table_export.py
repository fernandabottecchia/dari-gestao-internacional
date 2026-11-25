"""Exportacao somente leitura da tabela visivel de Incoming (inclui provisórios).

As colunas acompanham o template incoming.html. A coluna de acoes fica fora do
Excel. A selecao por indices utiliza a logica ja existente do DARI.
"""
from __future__ import annotations

COLUMNS = [
    "Nome", "DRE", "Passaporte", "E-mail", "Instituição de origem", "País de origem",
    "Curso", "Projeto", "Coordenador", "Semestre", "Duração", "Mobilidade",
    "Status", "Polimail", "Responsável", "E-mail do responsável",
    "Documento candidatura", "Acesso Intranet", "Form. DARI", "Plano de estudos",
    "Observações / Coordenação",
]


def _safe(value):
    """Evita interpretacao como formula Excel de campos provenientes da planilha."""
    if value is None:
        return "-"
    text = str(value)
    stripped = text.lstrip()
    if stripped.startswith(("=", "+", "-", "@")) and not (
        stripped.startswith("-") and stripped[1:].replace(".", "", 1).isdigit()
    ):
        return "'" + text
    return text


def build_incoming_table_report(ctx: dict, *, q: str, periodo: str, origem: str) -> dict:
    """Produz o mesmo recorte e a mesma ordem da tabela Incoming da interface."""
    data_rows = []
    for r in ctx["source_records"]:
        row = [
            r["name"], r["dre"], r["passport"], r["email"], r["institution"],
            r["country"], r["course"], r["program"], r["coordinator"],
            r["semester"], r["duration"], r["mobility_type"], r["status"],
            r["polimail"], r["responsible_name"], r["responsible_email"],
            r["candidate_document"], r["intranet_access"], r["dari_form"],
            r["study_plan"], r["notes"],
        ]
        data_rows.append([_safe(v) for v in row])

    recent = ctx["recent_provisional"]
    provenance = ctx["source_provenance"]
    approved = ctx["approved_university_labels"]
    recent_semesters = ctx["recent_semester_labels"]
    for m in ctx["rows"]:
        d = m.incoming_detail
        meta = recent.get(m.id) or {}
        from .incoming_consolidation_v53_33 import country as related_country
        country = related_country(m).name if related_country(m) else '-'
        institution = (approved.get(m.id) or
                       (m.home_university.name if m.home_university else "-"))
        if m.id in recent:
            situation = "Novo • aguardando homologação"
        elif (getattr(m,'_incoming_profile33',None) or {}).get('source_sha') not in (None,'historico'):
            situation = 'Consolidado da planilha'
        elif m.id in provenance:
            situation = "Vinculado à planilha"
        else:
            situation = "Cadastro histórico"
        status = m.status or "-"
        if m.id in recent and meta.get("normalized_administrative_status"):
            status += " • " + meta["normalized_administrative_status"]
        row = [
            m.student.full_name, m.student.dre or "-", m.student.passport or "-",
            m.student.email or "-", institution, country,
            (m.course.name if m.course else meta.get("source_course")) or "-",
            meta.get("source_program") or (m.program.name if m.program else "-"),
            meta.get("source_coordinator") or (m.coordinator.name if m.coordinator else "-"),
            ctx.get("period_labels",{}).get(m.id) or recent_semesters.get(m.id) or (m.start_semester.label if m.start_semester else "-"),
            m.duration_semesters if m.duration_semesters is not None else "-",
            m.mobility_type or "-", status,
            d.polimail if d and d.polimail else "-",
            d.responsible_name if d and d.responsible_name else "-",
            d.responsible_email if d and d.responsible_email else "-",
            d.candidate_document if d and d.candidate_document else "-",
            d.intranet_access if d and d.intranet_access else "-",
            d.dari_form if d and d.dari_form else "-",
            d.study_plan if d and d.study_plan else "-",
            d.coordination_notes if d and d.coordination_notes else (m.notes or "-"),

        ]
        data_rows.append([_safe(v) for v in row])

    for index,row in enumerate(data_rows):
        row.insert(1,row.pop(12))
    for index,m in enumerate(ctx["rows"], start=len(ctx["source_records"])):
        labels=ctx.get('status_labels',{}).get(m.id,[])
        if labels:data_rows[index][1]=' • '.join(label for _,label in labels)
        data_rows[index][10]=ctx.get('period_labels',{}).get(m.id) or data_rows[index][10]
    columns=list(COLUMNS);columns.insert(1,columns.pop(12))
    return {
        "title": "DARI — Incoming",
        "description": "Registros exibidos na tabela Incoming. Pré-cadastros e registros em revisão não são homologados.",
        "filters": [
            "Busca: " + (q or "Todos"),
            "Semestre: " + (periodo or "todos"),
            "Origem: " + (origem or "todos"),
        ],
        "columns": columns,
        "rows": data_rows,
    }
