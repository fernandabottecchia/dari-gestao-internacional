"""Emissão de minutas de cartas Incoming com conteúdo dos modelos fornecidos pela DARI.

Não insere assinatura de pessoa física nem afirma assinatura automática da UFRJ.
"""
from __future__ import annotations
from io import BytesIO
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
import re

from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_RIGHT
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Image, Paragraph, Spacer, PageBreak, KeepTogether
from sqlalchemy import text

MONTHS=('janeiro','fevereiro','março','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro')
FOOTER='Escola Politécnica da UFRJ • Diretoria Adjunta de Relações Internacionais — DARI\nAv. Athos da Silveira Ramos, 149 • Centro de Tecnologia, Bloco A, 2º andar • Cidade Universitária • Rio de Janeiro – RJ'
INTRO=[
'Caro aluno,',
'Você foi aceito como aluno da Escola Politécnica da UFRJ. Para todos nós, é uma grande satisfação perceber que a excelência de nossa escola tem atraído estudantes de ótimo nível provenientes de todas as instituições parceiras.',
'Esta é a primeira etapa da sua trajetória na UFRJ. Com a carta de aceite em anexo, será possível solicitar o visto de estudante para se tornar aluno regular no Brasil.',
'O plano de estudos enviado foi analisado; contudo, ele poderá sofrer alterações no início do semestre devido à disponibilidade de vagas.',
'Recomendamos que você chegue à UFRJ com pelo menos uma semana de antecedência, a fim de cumprir alguns trâmites administrativos.',
'Sugerimos fortemente que você utilize o período até sua chegada para intensificar os estudos da Língua Portuguesa, de modo a garantir que seu rendimento acadêmico não seja prejudicado pela falta de compreensão do idioma. No início do período acadêmico todos os estudantes aceitos pela Escola Politécnica da UFRJ deverão realizar a Avaliação Diagnóstica para verificar o nível de proficiência em português.',
'Ao chegar à UFRJ, procure a Diretoria Adjunta de Relações Internacionais (DARI), localizada no Bloco A, 2º andar, no Centro de Tecnologia, de segunda a sexta-feira, das 9h às 16h.',
'Bem-vindo à UFRJ!',
'Bons estudos!',
]

def labs_for_letter(db):
    return [dict(r) for r in db.execute(text('''SELECT l.id,l.name,l.acronym,l.coordinator FROM dari_laboratories_v53_46 l
        WHERE l.id NOT IN (SELECT record_id FROM dari_admin_deleted_v53_27 WHERE kind='laboratorios') ORDER BY l.name''')).mappings()]

def default_letter_values(m):
    u=m.home_university
    return {'name':m.student.full_name,'country':u.country.name if u and u.country else '',
            'university':u.name if u else '', 'course':m.course.name if m.course else '',
            'today':datetime.now(ZoneInfo('America/Sao_Paulo')).strftime('%Y-%m-%d')}

def _clean(form,key,maxlen=350,required=True):
    v=str(form.get(key,'')).strip()
    if required and not v:raise ValueError('Preencha o campo: '+key)
    if len(v)>maxlen or any(c in v for c in ('\x00','\r')):raise ValueError('Campo inválido: '+key)
    return v

def prepare_letter(m,form,db):
    if m.direction!='incoming':raise ValueError('Cartas de aceite disponíveis apenas para Incoming.')
    typ=_clean(form,'tipo',25)
    if typ not in ('geral','laboratorio'):raise ValueError('Selecione o modelo Geral ou Laboratório.')
    d=default_letter_values(m)
    record={'type':typ,'name':_clean(form,'nome',220),'country':_clean(form,'pais',150),
        'university':_clean(form,'universidade',250),'course':_clean(form,'curso',180,required=typ=='geral'),
        'supervisors':'','lab':'','lab_id':None}
    for key in ('inicio','fim','data_emissao'):
        raw=_clean(form,key,10)
        try:record[key]=date.fromisoformat(raw)
        except ValueError:raise ValueError('Data inválida: '+key)
    if record['fim']<record['inicio']:raise ValueError('A data de fim deve ser posterior à data de início.')
    if typ=='laboratorio':
        try:lab_id=int(_clean(form,'laboratorio_id',12))
        except ValueError:raise ValueError('Selecione um laboratório válido.')
        labs={x['id']:x for x in labs_for_letter(db)}
        if lab_id not in labs:raise ValueError('Laboratório não encontrado ou excluído.')
        record['lab']=labs[lab_id]['name'];record['lab_id']=lab_id
        record['supervisors']=_clean(form,'supervisores',500)
    return record

def _long(d):return f'{d.day:02d} de {MONTHS[d.month-1]} de {d.year}'

def paragraphs(data):
    name=data['name'];country=data['country'];university=data['university']
    if data['type']=='geral':
        first=f'Declaro pela presente que o(a) aluno(a) {name}, de {country}, foi aceito(a) para realizar um período de intercâmbio no curso de {data["course"]} da Escola Politécnica da Universidade Federal do Rio de Janeiro.'
    else:
        first=f'Declaro pela presente que o(a) aluno(a) {name}, de {country}, foi aceito(a) para realizar um estágio no {data["lab"]} da UFRJ, sob a supervisão de {data["supervisors"]}.'
    second=f'A vinda do(a) Sr(a) {name}, de {country}, está amparada pelo acordo de cooperação firmado entre {university}, em {country}, e a Universidade Federal do Rio de Janeiro.'
    third=f'O período de estudos do(a) Sr(a) {name} no Brasil vai de {_long(data["inicio"])} até {_long(data["fim"])} e todas as despesas com estadia serão custeadas pelo(a) aluno(a), incluindo-se aí as despesas com moradia, alimentação, seguro-saúde, etc.'
    return [first,second,third]

def _docx(data,header_image):
    doc=Document();sec=doc.sections[0]
    sec.top_margin=Cm(1.4);sec.bottom_margin=Cm(2.4);sec.left_margin=Cm(2.5);sec.right_margin=Cm(2.5)
    base=doc.styles['Normal'];base.font.name='Times New Roman';base.font.size=Pt(12)
    base.paragraph_format.space_after=Pt(10);base.paragraph_format.line_spacing=1.25
    header=sec.header.paragraphs[0];header.alignment=WD_ALIGN_PARAGRAPH.CENTER
    if header_image.exists():header.add_run().add_picture(str(header_image),width=Cm(15.5))
    sec.header_distance=Cm(0.4)
    footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.CENTER
    rr=footer.add_run(FOOTER);rr.font.size=Pt(7.2);rr.font.color.rgb=RGBColor(100,100,100)
    if data['type']=='geral':
        for i,line in enumerate(INTRO):
            p=doc.add_paragraph(line);p.alignment=WD_ALIGN_PARAGRAPH.LEFT if i in (0,7,8) else WD_ALIGN_PARAGRAPH.JUSTIFY
        doc.add_page_break()
    p=doc.add_paragraph('Carta de Aceite');p.alignment=WD_ALIGN_PARAGRAPH.CENTER
    p.runs[0].bold=True;p.runs[0].font.size=Pt(17)
    p.paragraph_format.space_before=Pt(24);p.paragraph_format.space_after=Pt(24)
    for line in paragraphs(data):
        p=doc.add_paragraph(line);p.alignment=WD_ALIGN_PARAGRAPH.JUSTIFY
        p.paragraph_format.first_line_indent=Cm(1.2)
    p=doc.add_paragraph(f'Rio de Janeiro, {_long(data["data_emissao"])}')
    p.alignment=WD_ALIGN_PARAGRAPH.RIGHT;p.paragraph_format.space_before=Pt(20)
    doc.add_paragraph('')
    p=doc.add_paragraph('________________________________________\nAssinatura da Direção / DARI')
    p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.space_before=Pt(32)
    out=BytesIO();doc.save(out);return out.getvalue()

def _pdf(data,header_image):
    font=Path('/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf')
    boldfont=Path('/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf')
    if not font.exists():font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf');boldfont=Path('/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf')
    # Windows installation can provide system font, or use included fallback.
    if not font.exists():
        import os
        fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
        font=fonts/'times.ttf';boldfont=fonts/'timesbd.ttf'
    if font.exists() and boldfont.exists():
        if 'Dari46Body' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('Dari46Body',str(font)))
            pdfmetrics.registerFont(TTFont('Dari46Bold',str(boldfont)))
        pdfmetrics.registerFontFamily('Dari46Body',normal='Dari46Body',bold='Dari46Bold')
    else:
        # ReportLab core fonts do not require external file dependencies.
        pdfmetrics.registerFontFamily('Dari46Body',normal='Times-Roman',bold='Times-Bold')
        pdfmetrics.registerFontFamily('Dari46Bold',normal='Times-Bold',bold='Times-Bold')
    body_font='Dari46Body' if font.exists() and boldfont.exists() else 'Times-Roman'
    bold_font='Dari46Bold' if font.exists() and boldfont.exists() else 'Times-Bold'
    normal=ParagraphStyle('Dari46normal',fontName=body_font,fontSize=11.2,leading=17,alignment=TA_JUSTIFY,spaceAfter=13,firstLineIndent=1.1*cm)
    center=ParagraphStyle('Dari46center',fontName=bold_font,fontSize=17,leading=21,alignment=TA_CENTER,spaceBefore=24,spaceAfter=24)
    right=ParagraphStyle('Dari46right',parent=normal,alignment=TA_RIGHT,firstLineIndent=0,spaceBefore=24)
    small=ParagraphStyle('Dari46left',parent=normal,alignment=TA_JUSTIFY,firstLineIndent=0)
    out=BytesIO()
    def page(canvas,doc):
        canvas.saveState();canvas.setFont(body_font,7.1);canvas.setFillColor(colors.HexColor('#555555'))
        for i,line in enumerate(FOOTER.split('\n')):
            canvas.drawCentredString(A4[0]/2,1.30*cm-i*.35*cm,line)
        canvas.restoreState()
    pdf=SimpleDocTemplate(out,pagesize=A4,leftMargin=2.5*cm,rightMargin=2.5*cm,topMargin=2.5*cm,bottomMargin=2.65*cm)
    story=[]
    def header():
        if header_image.exists():
            from PIL import Image as PILImage
            with PILImage.open(header_image) as im:w,h=im.size
            story.append(Image(str(header_image),width=15.5*cm,height=15.5*cm*h/w))
            story.append(Spacer(1,.2*cm))
    if data['type']=='geral':
        header()
        for i,line in enumerate(INTRO):story.append(Paragraph(escape(line),small))
        story.append(PageBreak())
    header();story.append(Paragraph('Carta de Aceite',center))
    for line in paragraphs(data):story.append(Paragraph(escape(line),normal))
    story.append(Paragraph('Rio de Janeiro, '+escape(_long(data['data_emissao'])),right))
    story.append(Spacer(1,1.1*cm));story.append(Paragraph('________________________________________',center))
    story.append(Paragraph('Assinatura da Direção / DARI',small))
    pdf.build(story,onFirstPage=page,onLaterPages=page)
    return out.getvalue()

def generate(data,header_image,formato):
    if formato=='docx':return _docx(data,header_image)
    if formato=='pdf':return _pdf(data,header_image)
    raise ValueError('Formato inválido')
