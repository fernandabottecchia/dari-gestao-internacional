from __future__ import annotations
import re
from .mobility_periods_v53_29 import modality as _modality29, key as _modality_key29

from collections import Counter
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from typing import Iterable
from urllib.parse import urlencode

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload, selectinload

from .agreement_progress import normalize_progress, progress_order
from .models import Agreement, Country, Course, Mobility, Semester, University
from .university_history import predecessors, ultimate_successors
from .incoming_consolidation_v53_33 import attach as attach_incoming, country as related_country

# Os relatórios agregados abaixo alimentam os cartões/gráficos do Painel Executivo.
# Os dois itens em DETAIL_REPORT_DEFINITIONS são os únicos exibidos na seção
# "Relatórios detalhados" para evitar repetir os mesmos gráficos do painel.
DASHBOARD_REPORT_DEFINITIONS = [
    {
        "slug": "mobilidade-semestre",
        "title": "Mobilidade por semestre",
        "description": "Evolução de Incoming e Outgoing ao longo dos períodos selecionados.",
    },
    {
        "slug": "mobilidade-pais",
        "title": "Mobilidade por país",
        "description": "Distribuição da mobilidade por país de origem (Incoming) ou destino (Outgoing).",
    },
    {
        "slug": "mobilidade-curso",
        "title": "Mobilidade por curso",
        "description": "Quantidade de processos por curso da UFRJ.",
    },
    {
        "slug": "mobilidade-universidade",
        "title": "Mobilidade por universidade",
        "description": "Instituições parceiras com maior volume de mobilidade.",
    },
    {
        "slug": "mobilidade-tipo",
        "title": "Tipos de mobilidade",
        "description": "Distribuição entre intercâmbio simples, duplo diploma e outros tipos cadastrados.",
    },
    {
        "slug": "acordos-andamento",
        "title": "Acordos por andamento",
        "description": "Negociação, elaboração, tramitação e assinatura dos acordos.",
    },
]

DETAIL_REPORT_DEFINITIONS = [
    {
        "slug": "mobilidades",
        "title": "Relatório detalhado de mobilidades",
        "description": "Lista de processos de Incoming e/ou Outgoing conforme os filtros aplicados.",
        "group": "Mobilidades",
    },
    {
        "slug": "acordos",
        "title": "Relatório detalhado de acordos",
        "description": "Lista dos acordos institucionais e respectivas situações de tramitação.",
        "group": "Acordos",
    },
]

_ALL_REPORTS = DASHBOARD_REPORT_DEFINITIONS + DETAIL_REPORT_DEFINITIONS
REPORT_MAP = {item["slug"]: item for item in _ALL_REPORTS}


def mobility_query_options():
    return (
        joinedload(Mobility.student),
        joinedload(Mobility.course),
        joinedload(Mobility.program),
        joinedload(Mobility.coordinator),
        joinedload(Mobility.home_university).joinedload(University.country),
        joinedload(Mobility.destination_university).joinedload(University.country),
        joinedload(Mobility.start_semester),
        joinedload(Mobility.end_semester),
        joinedload(Mobility.incoming_detail),
        joinedload(Mobility.outgoing_detail),
    )


def semester_key(semester: Semester | None) -> tuple[int, int]:
    if not semester:
        return (0, 0)
    return (semester.year, semester.term)


def related_university(m: Mobility) -> University | None:
    return m.home_university if m.direction == "incoming" else m.destination_university


def filter_query_string(filters: dict) -> str:
    values = {k: v for k, v in filters.items() if v not in (None, "") and (v != "all" or k in {"university_chart_limit", "category_chart_limit"})}
    if filters.get("start_semester") and not filters.get("end_semester"):
        values["end_semester"] = ""
    return urlencode(values)




def _split_csv(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = []
        for item in value:
            raw.extend(_split_csv(item))
        return raw
    return [part.strip() for part in str(value).split(",") if part and part.strip()]


def _ints_from_csv(value) -> list[int]:
    result = []
    seen = set()
    for part in _split_csv(value):
        if part.lower() == 'all':
            continue
        try:
            n = int(part)
        except (TypeError, ValueError):
            continue
        if n not in seen:
            seen.add(n)
            result.append(n)
    return result


def _normalized_set(value) -> set[str]:
    return {part.strip().casefold() for part in _split_csv(value) if part.strip() and part.strip().casefold() != 'all'}

def _as_int(value):
    try:
        return int(value) if value not in (None, "", "all") else None
    except (TypeError, ValueError):
        return None


def get_filter_options(db: Session) -> dict:
    from .editing_v53_30 import sorted_named
    from .period_filters_v53_41 import semester_options
    result = {
        "directions": ["incoming","outgoing"],
        "semesters": semester_options(db),
        "courses": db.scalars(select(Course).order_by(Course.name)).all(),
        "countries": [c for c in db.scalars(select(Country).order_by(Country.name)).all() if "pais nao identificado no access" not in _modality_key29(c.name)],
        "universities": db.scalars(select(University).order_by(University.name)).all(),
        "mobility_types": sorted({_modality29(str(v).strip()) for v in db.scalars(
            select(Mobility.mobility_type).where(Mobility.mobility_type.is_not(None)).distinct()
        ).all() if str(v).strip()}, key=lambda s: s.casefold()),
    }

    from types import SimpleNamespace
    for field in ('courses','countries','universities'):
        result[field].append(SimpleNamespace(id=0,name='Não informado'))
    for field in ('courses','countries','universities','mobility_types'):
        result[field]=sorted_named(result[field])
    return result


def _selected_semesters(semesters: list[Semester], filters: dict) -> list[Semester]:
    """Retorna o recorte de períodos.

    Regra da v0.7: quando o período final estiver vazio, o recorte é somente o
    período inicial. Quando ambos forem informados, usa o intervalo inclusivo.
    """
    if not semesters:
        return []
    if not filters.get("start_semester") and not filters.get("end_semester"):return semesters
    # Compare chronological values, not positions in the registered catalog.
    # Forecast-only boundaries may legitimately have no Semester row yet.
    def period_key(value):
        match = re.fullmatch(r"(\d{4})[./-]([1-3])", str(value or '').strip())
        return tuple(map(int, match.groups())) if match else None
    ordered = sorted((s for s in semesters if period_key(s.label)), key=lambda s:period_key(s.label))
    if not ordered:
        return []
    start_label = filters.get('start_semester') or ordered[0].label
    end_label = filters.get('end_semester') or start_label
    start, end = period_key(start_label), period_key(end_label)
    if start is None or end is None:
        raise ValueError('Período inválido. Use ano/semestre.')
    lo, hi = sorted((start, end))
    return [s for s in ordered if lo <= period_key(s.label) <= hi]


def filter_summary(filters: dict, options: dict | None = None) -> list[str]:
    result: list[str] = []
    start = filters.get("start_semester")
    end = filters.get("end_semester")
    if start:
        if end and end != start:
            result.append(f"Período: {start} a {end}")
        else:
            result.append(f"Período: {start}")

    directions = [d for d in _split_csv(filters.get("direction") or "all") if d and d not in {"all", "__none__"}]
    if directions:
        result.append("Mobilidade: " + ", ".join(d.title() for d in directions))

    if any(str(filters.get(k) or '').strip() == '__none__' for k in
           ('direction', 'course_id', 'country_id', 'university_id', 'mobility_type')):
        result.append('Nenhuma opção selecionada')

    if options:
        def names_from_ids(raw, bucket):
            ids = set(_ints_from_csv(raw))
            return [x.name for x in options.get(bucket, []) if x.id in ids]

        course_names = names_from_ids(filters.get("course_id"), "courses")
        if course_names and len(course_names) != len(options.get("courses", [])):
            result.append("Curso: " + (course_names[0] if len(course_names) == 1 else f"{len(course_names)} selecionados"))

        country_names = names_from_ids(filters.get("country_id"), "countries")
        if country_names and len(country_names) != len(options.get("countries", [])):
            result.append("País: " + (country_names[0] if len(country_names) == 1 else f"{len(country_names)} selecionados"))

        university_names = names_from_ids(filters.get("university_id"), "universities")
        if university_names and len(university_names) != len(options.get("universities", [])):
            result.append("Universidade: " + (university_names[0] if len(university_names) == 1 else f"{len(university_names)} selecionadas"))

        mobility_types = _split_csv(filters.get("mobility_type"))
        if mobility_types and len(mobility_types) != len(options.get("mobility_types", [])):
            result.append("Modalidade: " + (mobility_types[0] if len(mobility_types) == 1 else f"{len(mobility_types)} selecionadas"))
    else:
        mobility_types = _split_csv(filters.get("mobility_type"))
        if mobility_types:
            result.append("Modalidade: " + (mobility_types[0] if len(mobility_types) == 1 else f"{len(mobility_types)} selecionadas"))

    return result or ["Sem filtros adicionais"]


def load_mobilities(db: Session, filters: dict, force_direction: str | None = None) -> list[Mobility]:
    rows = db.scalars(select(Mobility).options(*mobility_query_options())).all()
    semesters = db.scalars(select(Semester).order_by(Semester.year, Semester.term)).all()
    selected = _selected_semesters(semesters, filters)
    allowed_semesters = {s.id for s in selected}
    active_filter = str(filters.get('process_active','')).lower() in ('1','true','yes')
    from .mobility_periods_v53_29 import presence
    period_map = presence(db,rows)
    from .mobility_periods_v53_29 import activity
    active_ids = activity(db,rows) if active_filter else set()
    allowed_labels = {s.label for s in selected}
    attach_incoming(db,rows)
    for m in rows:m._active_periods29 = period_map.get(m.id,set())

    if any(str(filters.get(k) or '').strip() == '__none__' for k in
           ('direction', 'course_id', 'country_id', 'university_id', 'mobility_type')):
        return []
    directions = {force_direction} if force_direction in {"incoming", "outgoing"} else set(_split_csv(filters.get("direction") or "all"))
    directions = {d for d in directions if d and d != "all"}
    course_ids = set(_ints_from_csv(filters.get("course_id")))
    country_ids = set(_ints_from_csv(filters.get("country_id")))
    university_ids = set(_ints_from_csv(filters.get("university_id")))
    mobility_types = _normalized_set(filters.get("mobility_type"))

    allowed_university_ids = None
    if university_ids:
        allowed_university_ids = set()
        for uid in university_ids:
            allowed_university_ids.update({0} if uid==0 else predecessors(db, uid))

    result: list[Mobility] = []
    for m in rows:
        if active_filter and m.id not in active_ids:continue
        if m.direction == 'incoming' and m.status == 'Pré-cadastro — planilha (não homologado)':
            continue
        if directions and m.direction not in directions:
            continue
        if mobility_types and (_modality29(m.mobility_type) or "Não informado").strip().casefold() not in mobility_types:
            continue
        if m.start_semester_id not in allowed_semesters and not (m.start_semester_id is None and len(allowed_semesters)==len(semesters)):
            continue
        if course_ids and (m.course_id or 0) not in course_ids:
            continue
        uni = related_university(m)
        if allowed_university_ids and (uni.id if uni else 0) not in allowed_university_ids:
            continue
        if country_ids and (related_country(m).id if related_country(m) else 0) not in country_ids:
            continue
        result.append(m)
    return result


def load_agreements(db: Session, filters: dict) -> list[Agreement]:
    rows = db.scalars(
        select(Agreement).options(
            joinedload(Agreement.university).joinedload(University.country),
            selectinload(Agreement.actions),
        ).where(Agreement.id.not_in(select(text('agreement_id')).select_from(text('dari_deleted_agreements_v53_46'))))
    ).all()
    if any(str(filters.get(k) or '').strip() == '__none__' for k in
           ('direction', 'course_id', 'country_id', 'university_id', 'mobility_type')):
        return []
    country_ids = set(_ints_from_csv(filters.get("country_id")))
    university_ids = set(_ints_from_csv(filters.get("university_id")))
    allowed_university_ids = None
    if university_ids:
        allowed_university_ids = set()
        for uid in university_ids:
            allowed_university_ids.update({0} if uid==0 else predecessors(db, uid))
    result = []
    for a in rows:
        if allowed_university_ids and a.university_id not in allowed_university_ids:
            continue
        if country_ids and (not a.university or a.university.country_id not in country_ids):
            continue
        result.append(a)
    return result


def count_by(items: Iterable, label_fn) -> list[dict]:
    counts = Counter(label_fn(x) or "Não informado" for x in items)
    return [
        {"label": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ]


def simple_chart(title: str, grouped: list[dict], chart_type: str = "bar", orientation: str = "vertical") -> dict:
    return {
        "title": title,
        "type": chart_type,
        "orientation": orientation,
        "labels": [x["label"] for x in grouped],
        "datasets": [{"label": "Quantidade", "data": [x["count"] for x in grouped]}],
    }


def semester_chart(mobilities: list[Mobility], semesters: list[Semester], filters: dict) -> dict:
    selected = _selected_semesters(semesters, filters)
    labels: list[str] = []
    incoming: list[int] = []
    outgoing: list[int] = []
    directions = {d for d in _split_csv(filters.get("direction") or "all") if d in {"incoming", "outgoing"}}

    for sem in selected:
        sem_rows = [m for m in mobilities if m.start_semester_id == sem.id]
        labels.append(sem.label)
        incoming.append(sum(1 for m in sem_rows if m.direction == "incoming"))
        outgoing.append(sum(1 for m in sem_rows if m.direction == "outgoing"))

    datasets = []
    if not directions or "incoming" in directions:
        datasets.append({"label": "Incoming", "data": incoming})
    if not directions or "outgoing" in directions:
        datasets.append({"label": "Outgoing", "data": outgoing})
    return {
        "title": "Mobilidade por semestre",
        "type": "bar",
        "orientation": "vertical",
        "labels": labels,
        "datasets": datasets,
    }


MOBILITY_DETAIL_COLUMNS = ["Fluxo", "Nome", "DRE", "Curso", "Universidade", "País", "Semestre", "Mobilidade", "Status"]
AGREEMENT_DETAIL_COLUMNS = ["Universidade", "País", "Tipo", "SIG+", "SEI", "Andamento", "Status", "Validade"]


def mobility_detail_rows(mobilities: list[Mobility]) -> list[dict]:
    """Mesmas nove colunas do relatório detalhado existente, sem duplicar mobilidades."""
    result = []
    for m in sorted(mobilities, key=lambda x: (semester_key(x.start_semester), (x.student.full_name or "").casefold(), x.id)):
        uni = related_university(m)
        result.append({"mobility_id": m.id, "cells": [
            m.direction.title(), m.student.full_name,
            m.student.dre or "", m.course.name if m.course else "",
            uni.name if uni else "", related_country(m).name if related_country(m) else "",
            m.start_semester.label if m.start_semester else "",
            m.mobility_type or "", m.status or "",
        ]})
    return result


def agreement_detail_rows(agreements: list[Agreement]) -> list[dict]:
    return [{"agreement_id": a.id, "cells": [
        a.university.name if a.university else "",
        a.university.country.name if a.university and a.university.country else "",
        a.agreement_type or "", a.sig_number or "", a.sei_number or "",
        a.progress or "", a.status or "",
        a.expiry_date.strftime("%d/%m/%Y") if a.expiry_date else "",
    ]} for a in agreements]


def dashboard_data(db: Session, filters: dict) -> dict:
    options = get_filter_options(db)
    mobilities = load_mobilities(db, filters)
    incoming_rows = [m for m in mobilities if m.direction == "incoming"]
    outgoing_rows = [m for m in mobilities if m.direction == "outgoing"]
    agreements = load_agreements(db, {})

    by_country = count_by(
        mobilities,
        lambda m: related_country(m).name
        if related_country(m)
        else None,
    )
    by_course = count_by(mobilities, lambda m: m.course.name if m.course else None)
    successors = ultimate_successors(db)
    university_names = {u.id: u.name for u in options['universities']}
    by_university = count_by(mobilities, lambda m:
        university_names.get(successors.get(related_university(m).id, related_university(m).id))
        if related_university(m) else None)
    by_type = count_by(mobilities, lambda m: m.mobility_type)
    agreement_progress = sorted(count_by(agreements, lambda a: normalize_progress(a.progress)), key=lambda item: (progress_order(item["label"]), item["label"]))

    charts = {
        "semester": semester_chart(mobilities, options["semesters"], filters),
        "country": simple_chart("Mobilidade por país", by_country[:10], "bar", "horizontal"),
        "course": simple_chart("Mobilidade por curso", by_course[:10], "bar", "horizontal"),
        "university": simple_chart("Mobilidade por universidade", by_university[:10], "bar", "horizontal"),
        "type": simple_chart("Tipos de mobilidade", by_type, "doughnut", "vertical"),
        "agreement": simple_chart("Acordos por andamento", agreement_progress, "bar", "horizontal"),
    }

    represented_universities = {successors.get(related_university(m).id, related_university(m).id)
                                for m in mobilities if related_university(m)}
    return {
        "options": options,
        "mobilities": mobilities,
        "agreements_rows": agreements,
        "incoming_count": len(incoming_rows),
        "outgoing_count": len(outgoing_rows),
        "mobility_count": len(mobilities),
        "represented_universities": len(represented_universities),
        "agreements_active": sum(1 for a in agreements if a.status == "Ativo"),
        "agreements_in_progress": sum(1 for a in agreements if a.status != "Ativo"),
        "charts": charts,
        "filter_summary": filter_summary(filters, options),
    }


def _limited_category_chart(result: dict, title: str, grouped: list[dict], filters: dict) -> dict:
    """Limita somente as barras; todas as categorias permanecem em result['rows'].

    No modo Todas, o PDF mostra as barras em páginas separadas para não
    sobrepor rótulos. Os filtros globais já foram aplicados ao grouped.
    """
    limit = str(filters.get("category_chart_limit") or "10").strip().lower()
    if limit not in {"3", "5", "10", "all"}:
        limit = "10"
    result["category_chart_limit"] = limit
    result["total_categories"] = len(grouped)
    shown = grouped if limit == "all" else grouped[:int(limit)]
    return simple_chart(title, shown, "bar", "horizontal")


def build_report(db: Session, slug: str, filters: dict) -> dict:
    if slug not in REPORT_MAP:
        raise KeyError(slug)
    definition = REPORT_MAP[slug]
    options = get_filter_options(db)
    result = {
        "slug": slug,
        "title": definition["title"],
        "description": definition["description"],
        "filters": filter_summary(filters, options),
        "columns": [],
        "rows": [],
        "chart": None,
    }

    if slug in {"acordos", "acordos-andamento"}:
        agreements = load_agreements(db, filters)
        grouped = sorted(count_by(agreements, lambda a: normalize_progress(a.progress)), key=lambda item: (progress_order(item["label"]), item["label"]))
        result["chart"] = simple_chart("Acordos por andamento", grouped, "bar", "horizontal")
        if slug == "acordos-andamento":
            result["columns"] = ["Andamento", "Acordos"]
            result["rows"] = [[x["label"], x["count"]] for x in grouped]
            return result

        result["columns"] = ["Universidade", "País", "Tipo", "SIG+", "SEI", "Andamento", "Status", "Validade"]
        result["rows"] = [
            [
                a.university.name if a.university else "",
                a.university.country.name if a.university and a.university.country else "",
                a.agreement_type or "",
                a.sig_number or "",
                a.sei_number or "",
                normalize_progress(a.progress) or "",
                a.status or "",
                a.expiry_date.strftime("%d/%m/%Y") if a.expiry_date else "",
            ]
            for a in agreements
        ]
        return result

    mobilities = load_mobilities(db, filters)

    if slug == "mobilidades":
        direction = filters.get("direction") or "all"
        if direction == "incoming":
            result["title"] = "Relatório detalhado de Incoming"
            result["description"] = "Lista dos processos de estudantes estrangeiros conforme os filtros aplicados."
        elif direction == "outgoing":
            result["title"] = "Relatório detalhado de Outgoing"
            result["description"] = "Lista dos processos de estudantes brasileiros conforme os filtros aplicados."
        result["columns"] = MOBILITY_DETAIL_COLUMNS
        result["rows"] = [r["cells"] for r in mobility_detail_rows(mobilities)]
        result["chart"] = semester_chart(mobilities, options["semesters"], filters)
        return result

    if slug == "mobilidade-semestre":
        spec = semester_chart(mobilities, options["semesters"], filters)
        result["chart"] = spec
        options_by_label = {s.label: s.id for s in options["semesters"]}
        other_counts = {label: sum(1 for m in mobilities
                         if m.start_semester_id == options_by_label[label]
                         and m.direction not in {"incoming", "outgoing"})
                        for label in spec["labels"]}
        result["columns"] = ["Semestre"] + [ds["label"] for ds in spec["datasets"]] + ["Total"]
        rows = []
        for idx, label in enumerate(spec["labels"]):
            values = [ds["data"][idx] for ds in spec["datasets"]]
            other = other_counts[label]
            rows.append([label, *values, sum(values) + other])
        rows.append(["Total", *[sum(r[i] for r in rows) for i in range(1,len(result["columns"]))]])
        result["rows"] = rows
        return result

    if slug == "mobilidade-pais":
        grouped = count_by(
            mobilities,
            lambda m: related_country(m).name
            if related_country(m)
            else None,
        )
        result["columns"] = ["País", "Processos"]
        result["chart"] = _limited_category_chart(result, definition["title"], grouped, filters)
    elif slug == "mobilidade-curso":
        grouped = count_by(mobilities, lambda m: m.course.name if m.course else None)
        result["columns"] = ["Curso", "Processos"]
        result["chart"] = _limited_category_chart(result, definition["title"], grouped, filters)
    elif slug == "mobilidade-universidade":
        successors = ultimate_successors(db)
        canonical_names = {u.id: u.name for u in options['universities']}
        grouped = count_by(mobilities, lambda m:
            canonical_names.get(successors.get(related_university(m).id, related_university(m).id))
            if related_university(m) else None)
        result["columns"] = ["Universidade", "Processos"]
        # O controle afeta somente o gráfico; a tabela sempre contém todas as universidades.
        limit = str(filters.get("university_chart_limit") or "10").strip().lower()
        if limit not in {"3", "5", "10"}:
            limit = "10"
        displayed = grouped[:int(limit)]
        chart_note = f"Gráfico: {len(displayed)} primeiras instituições."
        result["chart"] = simple_chart(definition["title"], displayed, "bar", "horizontal")
        result["chart_note"] = chart_note
        result["university_chart_limit"] = limit
        result["total_universities"] = len(grouped)
    elif slug == "mobilidade-tipo":
        grouped = count_by(mobilities, lambda m: m.mobility_type)
        result["columns"] = ["Tipo de mobilidade", "Processos"]
        result["chart"] = simple_chart(definition["title"], grouped, "doughnut", "vertical")
    else:
        grouped = []
    result["rows"] = [[x["label"], x["count"]] for x in grouped]
    if slug in ('mobilidade-pais','mobilidade-curso','mobilidade-universidade','mobilidade-tipo'):
        noun={'mobilidade-pais':'países','mobilidade-curso':'cursos','mobilidade-universidade':'universidades','mobilidade-tipo':'tipos de mobilidade'}[slug]
        categories=sum(x['label']!='Não informado' for x in grouped)
        result['rows'].append([f'Total — {categories} {noun}',sum(x['count'] for x in grouped)])
    return result


def report_to_xlsx(report: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório"
    ws.append([report["title"]])
    ws["A1"].font = Font(size=16, bold=True)
    ws.append([report["description"]])
    ws.append(["Filtros:", " | ".join(report["filters"])])
    ws.append([])
    ws.append(report["columns"])
    for cell in ws[5]:
        cell.font = Font(bold=True)
    for row in report["rows"]:
        ws.append(row)
    for col_idx in range(1, len(report["columns"]) + 1):
        values = [str(ws.cell(r, col_idx).value or "") for r in range(1, ws.max_row + 1)]
        width = min(max(max((len(v) for v in values), default=10) + 2, 12), 48)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(cell.value,str):cell.data_type="s"
    ws.freeze_panes = "A6"
    ws.auto_filter.ref = f"A5:{get_column_letter(max(1, len(report['columns'])))}{ws.max_row}"
    if report.get("slug") == "mobilidade-universidade":
        from openpyxl.chart import BarChart, Reference
        graph = report.get("chart") or {}
        chart_labels = graph.get("labels", [])
        chart_values = graph.get("datasets", [{}])[0].get("data", []) if graph.get("datasets") else []
        chart_ws = wb.create_sheet("Gráfico")
        chart_ws["A1"] = "Mobilidade por universidade"
        chart_ws["A1"].font = Font(size=16, bold=True)
        chart_ws["A2"] = report.get("chart_note", "")
        chart_ws["A3"] = "A aba Relatório contém todas as universidades do recorte."
        chart_ws.append([])
        chart_ws.append(["Universidade", "Processos"])
        chart_ws["A5"].font = Font(bold=True)
        chart_ws["B5"].font = Font(bold=True)
        for label, count in zip(chart_labels, chart_values):
            chart_ws.append([label, count])
        chart_ws.column_dimensions["A"].width = 52
        chart_ws.column_dimensions["B"].width = 16
        chart_ws.column_dimensions["D"].width = 14
        if chart_labels:
            bar = BarChart()
            bar.type = "bar"
            bar.grouping = "clustered"
            bar.title = "Mobilidades por universidade"
            bar.y_axis.title = "Instituição"
            bar.x_axis.title = "Processos"
            bar.style = 10
            bar.height = max(9.5, len(chart_labels) * 0.85)
            bar.width = 23
            bar.add_data(Reference(chart_ws, min_col=2, min_row=5, max_row=5+len(chart_labels)), titles_from_data=True)
            bar.set_categories(Reference(chart_ws, min_col=1, min_row=6, max_row=5+len(chart_labels)))
            chart_ws.add_chart(bar, "D5")
        chart_ws.freeze_panes = "A6"
        # A primeira aba continua sendo a listagem completa, sem limitar as linhas.
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _chart_png(chart: dict) -> bytes:
    """Renderiza o mesmo tipo de gráfico do painel para inclusão no PDF."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = chart.get("labels", [])
    datasets = chart.get("datasets", [])
    chart_type = chart.get("type", "bar")
    orientation = chart.get("orientation", "vertical")

    fig, ax = plt.subplots(figsize=(10.2, 4.6), constrained_layout=True)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    colors = ["#477d82", "#8aaeb1", "#9b8b6f", "#786d93", "#6e9278", "#9b6f76", "#6d8493", "#a0927f"]

    if chart_type == "line":
        x = list(range(len(labels)))
        for idx, ds in enumerate(datasets):
            ax.plot(x, ds.get("data", []), marker="o", linewidth=2.0, label=ds.get("label", "Série"), color=colors[idx % len(colors)])
        ax.set_xticks(x, labels, rotation=0)
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=0.18)
        if datasets:
            ax.legend(frameon=False)
    elif chart_type == "doughnut":
        values = datasets[0].get("data", []) if datasets else []
        if sum(values) > 0:
            ax.pie(
                values,
                labels=labels,
                colors=colors[: max(1, len(values))],
                startangle=90,
                counterclock=False,
                autopct=lambda p: f"{p:.0f}%" if p >= 5 else "",
                wedgeprops={"width": 0.45, "edgecolor": "white"},
                textprops={"fontsize": 8},
            )
            ax.axis("equal")
        else:
            ax.text(0.5, 0.5, "Sem dados para o recorte", ha="center", va="center", transform=ax.transAxes)
            ax.axis("off")
    elif orientation == "horizontal":
        values = datasets[0].get("data", []) if datasets else []
        y = list(range(len(labels)))
        ax.barh(y, values, color=colors[0])
        display_labels = ([label if len(str(label)) <= 48 else str(label)[:45] + "..." for label in labels]
                          if chart.get("title", "").startswith(("Mobilidade por universidade", "Mobilidade por curso", "Mobilidade por país")) else labels)
        ax.set_yticks(y, display_labels)
        ax.invert_yaxis()
        ax.set_xlim(left=0)
        ax.grid(axis="x", alpha=0.18)
        for pos, value in zip(y, values):
            ax.text(value + (max(values) * 0.01 if values else 0.05), pos, str(value), va="center", fontsize=8)
    else:
        # Barras agrupadas: Incoming e Outgoing permanecem visíveis também no PDF.
        # Cada semestre é uma categoria independente, sem interpolação.
        x = list(range(len(labels)))
        n_series = max(1, len(datasets))
        width = 0.78 / n_series
        for idx, ds in enumerate(datasets):
            positions = [pos - 0.39 + width * (idx + 0.5) for pos in x]
            ax.bar(positions, ds.get("data", []), width=width,
                   color=colors[idx % len(colors)], label=ds.get("label", "Série"))
        # Com muitos semestres, reduza somente os rótulos exibidos no eixo X.
        step = max(1, (len(labels) + 15) // 16)
        ax.set_xticks(x[::step], labels[::step], rotation=35, ha="right")
        ax.set_ylim(bottom=0)
        ax.grid(axis="y", alpha=0.18)
        if len(datasets) > 1:
            ax.legend(frameon=False)

    if chart_type != "doughnut":
        ax.spines[["top", "right"]].set_visible(False)
        ax.tick_params(labelsize=8)
    ax.set_title(chart.get("title", "Gráfico"), loc="left", fontsize=12, fontweight="bold", color="#172333")

    out = BytesIO()
    fig.savefig(out, format="png", dpi=150, facecolor="white")
    plt.close(fig)
    return out.getvalue()


def report_to_pdf(report: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title=report["title"],
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DariTitle",
        parent=styles["Title"],
        fontSize=18,
        leading=22,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#172333"),
    )
    small = ParagraphStyle(
        "DariSmall",
        parent=styles["BodyText"],
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#536070"),
    )
    cell = ParagraphStyle("DariCell", parent=styles["BodyText"], fontSize=7.3, leading=9)
    # No ReportLab, TableStyle.TEXTCOLOR não altera a cor de um Paragraph:
    # o cabeçalho precisa de um ParagraphStyle próprio. Use fundo claro e
    # texto escuro para manter contraste consistente em todos os relatórios.
    header_cell = ParagraphStyle(
        "DariHeaderCell", parent=cell,
        fontName="Helvetica-Bold", textColor=colors.HexColor("#172333"),
    )
    story = [
        Paragraph("DARI • Escola Politécnica • UFRJ", small),
        Paragraph(report["title"], title_style),
        Paragraph(report["description"], styles["BodyText"]),
        Spacer(1, 6),
        Paragraph("Filtros: " + " | ".join(report["filters"]), small),
        Spacer(1, 10),
    ]

    if report.get("chart") and report["chart"].get("labels"):
        if report.get("chart_note"):
            story.append(Paragraph(report["chart_note"], small))
            story.append(Spacer(1, 6))
        chart = report["chart"]
        is_all = (report.get("slug") in {"mobilidade-pais", "mobilidade-curso"}
                  and report.get("category_chart_limit") == "all")
        if is_all and len(chart["labels"]) > 10:
            # Gráfico completo, mas dividido em blocos legíveis de 10 categorias.
            total = len(chart["labels"])
            for offset in range(0, total, 10):
                end = min(offset + 10, total)
                part = {**chart, "labels": chart["labels"][offset:end],
                        "datasets": [{**ds, "data": ds["data"][offset:end]}
                                     for ds in chart["datasets"]],
                        "title": f"{report['title']} ({offset+1}–{end} de {total})"}
                if offset:
                    story.append(PageBreak())
                elif len(story):
                    story.append(Spacer(1, 6))
                chart_bytes = _chart_png(part)
                story.append(Image(BytesIO(chart_bytes), width=23.7 * cm, height=10.7 * cm))
            # Separar a tabela completa dos blocos de gráficos.
            story.append(PageBreak())
        else:
            chart_bytes = _chart_png(chart)
            story.append(Image(BytesIO(chart_bytes), width=23.7 * cm, height=10.7 * cm))
            story.append(Spacer(1, 10))

    if report["rows"]:
        data = [[Paragraph(escape(str(v)), header_cell) for v in report["columns"]]]
        for row in report["rows"]:
            data.append([Paragraph(escape(str(v)) if v is not None else "", cell) for v in row])
        usable = landscape(A4)[0] - 2.4 * cm
        col_widths = [usable / max(1, len(report["columns"]))] * max(1, len(report["columns"]))
        table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#edf2f7")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#172333")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9dee5")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f9fb")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("Nenhum registro encontrado para os filtros selecionados.", styles["BodyText"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Gerado pelo Sistema de Gestão de Mobilidade Internacional DARI em " + datetime.now().strftime("%d/%m/%Y %H:%M"), small))
    doc.build(story)
    return buffer.getvalue()


# DARI_V53_16_WORKFLOW - recorte de alunos comprovadamente classificados como ativos.
_from_workflow_load_mobilities = load_mobilities
_from_workflow_filter_summary = filter_summary

def load_mobilities(db, filters, force_direction=None):
    rows = _from_workflow_load_mobilities(db,filters,force_direction)
    if str(filters.get('process_active','')).lower() not in ('1','true','yes'):
        return rows
    from .mobility_workflow_v53_16 import active_ids
    ids=active_ids(db)
    return [m for m in rows if m.id in ids]

def filter_summary(filters, options=None):
    result=_from_workflow_filter_summary(filters,options)
    if str(filters.get('process_active','')).lower() in ('1','true','yes'):
        return result+['Apenas alunos ativos']
    return result
