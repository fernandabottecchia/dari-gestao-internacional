from __future__ import annotations

from io import BytesIO
from pathlib import Path
from datetime import date
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib import colors


def safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value.strip("_") or "documento"


def _mobility_values(m):
    university = m.home_university if m.direction == "incoming" else m.destination_university
    return {
        "name": m.student.full_name,
        "email": m.student.email or "-",
        "dre": m.student.dre or "-",
        "passport": m.student.passport or "-",
        "course": m.course.name if m.course else "-",
        "university": university.name if university else "-",
        "country": university.country.name if university and university.country else "-",
        "semester": m.start_semester.label if m.start_semester else "-",
        "mobility_type": m.mobility_type or "-",
        "duration": f"{m.duration_semesters} semestre(s)" if m.duration_semesters else "-",
    }


def generate_acceptance_docx(m, header_image: Path) -> bytes:
    data = _mobility_values(m)
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(1.5)
    section.bottom_margin = Cm(1.7)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    if header_image.exists():
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(str(header_image), width=Cm(16.0))

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("DOCUMENTO DE DEMONSTRAÇÃO - NÃO OFICIAL")
    r.bold = True
    r.font.size = Pt(10)
    r.font.color.rgb = RGBColor(180, 30, 30)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("CARTA DE ACEITE")
    r.bold = True
    r.font.size = Pt(16)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.space_after = Pt(10)
    p.add_run(
        "Este documento é um exemplo funcional criado para validar a automação do Sistema DARI. "
        "O texto institucional definitivo deverá ser substituído pelo modelo oficial aprovado pela Diretoria Adjunta de Relações Internacionais."
    )

    body = (
        f"Declaramos, para fins de demonstração do sistema, que {data['name']} foi registrado(a) "
        f"como estudante de mobilidade acadêmica na Escola Politécnica da Universidade Federal do Rio de Janeiro, "
        f"proveniente de {data['university']} ({data['country']}), para o período {data['semester']}. "
        f"O processo está associado ao curso {data['course']}, na modalidade {data['mobility_type']}, "
        f"com duração prevista de {data['duration']}."
    )
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.line_spacing = 1.25
    p.add_run(body)

    doc.add_paragraph()
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"
    fields = [
        ("Nome", data["name"]),
        ("Instituição de origem", data["university"]),
        ("País", data["country"]),
        ("Curso na UFRJ", data["course"]),
        ("Período", data["semester"]),
        ("Mobilidade", data["mobility_type"]),
        ("Duração prevista", data["duration"]),
    ]
    for label, value in fields:
        cells = table.add_row().cells
        cells[0].text = label
        cells[1].text = value
        cells[0].paragraphs[0].runs[0].bold = True

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(22)
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p.add_run(f"Rio de Janeiro, {date.today().strftime('%d/%m/%Y')}.")

    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(26)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Diretoria Adjunta de Relações Internacionais\nEscola Politécnica - UFRJ")
    r.bold = True

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("Documento sem assinatura institucional")
    r.italic = True
    r.font.size = Pt(9)
    r.font.color.rgb = RGBColor(100, 100, 100)

    bio = BytesIO()
    doc.save(bio)
    return bio.getvalue()


def generate_acceptance_pdf(m, header_image: Path) -> bytes:
    data = _mobility_values(m)
    bio = BytesIO()
    pdf = SimpleDocTemplate(
        bio, pagesize=A4, rightMargin=2.0*cm, leftMargin=2.0*cm,
        topMargin=1.4*cm, bottomMargin=1.6*cm,
        title="Carta de Aceite - Demonstração DARI",
        author="Sistema de Gestão de Mobilidade Internacional DARI",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("DariTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=16, leading=20, alignment=TA_CENTER, spaceAfter=12)
    warning = ParagraphStyle("Warning", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=colors.HexColor("#A11A1A"), alignment=TA_CENTER, spaceAfter=12)
    normal = ParagraphStyle("Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=10.5, leading=15, alignment=TA_JUSTIFY, spaceAfter=10)
    center = ParagraphStyle("Center", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=14, alignment=TA_CENTER)
    right = ParagraphStyle("Right", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=14, alignment=2)

    story = []
    if header_image.exists():
        im = Image(str(header_image))
        maxw = 16.5*cm
        ratio = maxw / im.imageWidth
        im.drawWidth = maxw
        im.drawHeight = im.imageHeight * ratio
        story += [im, Spacer(1, 0.2*cm)]

    story += [
        Paragraph("DOCUMENTO DE DEMONSTRAÇÃO - NÃO OFICIAL", warning),
        Paragraph("CARTA DE ACEITE", title),
        Paragraph(
            "Este documento é um exemplo funcional criado para validar a automação do Sistema DARI. "
            "O texto institucional definitivo deverá ser substituído pelo modelo oficial aprovado pela Diretoria Adjunta de Relações Internacionais.",
            normal,
        ),
        Paragraph(
            f"Declaramos, para fins de demonstração do sistema, que <b>{data['name']}</b> foi registrado(a) como estudante de mobilidade acadêmica "
            f"na Escola Politécnica da Universidade Federal do Rio de Janeiro, proveniente de <b>{data['university']}</b> ({data['country']}), "
            f"para o período <b>{data['semester']}</b>. O processo está associado ao curso <b>{data['course']}</b>, na modalidade "
            f"<b>{data['mobility_type']}</b>, com duração prevista de <b>{data['duration']}</b>.",
            normal,
        ),
        Spacer(1, 0.2*cm),
    ]

    fields = [
        ["Nome", data["name"]],
        ["Instituição de origem", data["university"]],
        ["País", data["country"]],
        ["Curso na UFRJ", data["course"]],
        ["Período", data["semester"]],
        ["Mobilidade", data["mobility_type"]],
        ["Duração prevista", data["duration"]],
    ]
    t = Table(fields, colWidths=[4.6*cm, 11.2*cm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.HexColor("#CBD2D9")),
        ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#F3F5F7")),
        ("FONTNAME", (0,0), (0,-1), "Helvetica-Bold"),
        ("FONTNAME", (1,0), (1,-1), "Helvetica"),
        ("FONTSIZE", (0,0), (-1,-1), 9.5),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 7),
        ("RIGHTPADDING", (0,0), (-1,-1), 7),
        ("TOPPADDING", (0,0), (-1,-1), 6),
        ("BOTTOMPADDING", (0,0), (-1,-1), 6),
    ]))
    story += [t, Spacer(1, 0.7*cm), Paragraph(f"Rio de Janeiro, {date.today().strftime('%d/%m/%Y')}.", right), Spacer(1, 1.0*cm)]
    story += [Paragraph("<b>Diretoria Adjunta de Relações Internacionais</b><br/>Escola Politécnica - UFRJ", center), Spacer(1, 0.2*cm), Paragraph("<i>Documento sem assinatura institucional</i>", center)]

    pdf.build(story)
    return bio.getvalue()
