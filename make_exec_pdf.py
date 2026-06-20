"""Render EXECUTIVE_SUMMARY.md -> a clean, professional PDF (reportlab)."""
import re, html
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
                                ListFlowable, ListItem)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

NAVY = HexColor('#0E2A47')
BLUE = HexColor('#1F6FB2')
GREY = HexColor('#444444')

ss = getSampleStyleSheet()
H1 = ParagraphStyle('H1', parent=ss['Title'], fontName='Helvetica-Bold',
                    fontSize=22, textColor=NAVY, spaceAfter=4, alignment=TA_LEFT, leading=26)
SUB = ParagraphStyle('SUB', parent=ss['Normal'], fontName='Helvetica',
                     fontSize=11, textColor=BLUE, spaceAfter=10)
H2 = ParagraphStyle('H2', parent=ss['Heading2'], fontName='Helvetica-Bold',
                    fontSize=14, textColor=NAVY, spaceBefore=12, spaceAfter=5, leading=17)
BODY = ParagraphStyle('BODY', parent=ss['Normal'], fontName='Helvetica',
                      fontSize=10.5, textColor=GREY, leading=15, spaceAfter=6)
BULLET = ParagraphStyle('BULLET', parent=BODY, leftIndent=6, spaceAfter=3)
QUOTE = ParagraphStyle('QUOTE', parent=BODY, leftIndent=14, textColor=NAVY,
                       fontName='Helvetica-Oblique', borderPadding=4, spaceAfter=8)
SMALL = ParagraphStyle('SMALL', parent=BODY, fontSize=8.5, textColor=HexColor('#888888'))


def fmt(t):
    t = html.escape(t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t)
    t = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<i>\1</i>', t)
    t = re.sub(r'`(.+?)`', r'<font face="Courier">\1</font>', t)
    return t


def build():
    lines = open('EXECUTIVE_SUMMARY.md', encoding='utf-8').read().split('\n')
    flow = []
    bullets = []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            flow.append(ListFlowable(
                [ListItem(Paragraph(fmt(b), BULLET), leftIndent=12,
                          value='bullet', bulletColor=BLUE) for b in bullets],
                bulletType='bullet', start='•', leftIndent=14))
            flow.append(Spacer(1, 4))
            bullets = []

    for ln in lines:
        s = ln.rstrip()
        if s.startswith('- '):
            bullets.append(s[2:]); continue
        flush_bullets()
        if not s.strip():
            continue
        if s.startswith('# '):
            flow.append(Paragraph(fmt(s[2:]), H1))
        elif s.startswith('## '):
            flow.append(Paragraph(fmt(s[3:]), H2))
        elif s.startswith('### '):
            flow.append(Paragraph(fmt(s[4:]), ParagraphStyle('H3', parent=H2, fontSize=12)))
        elif s.startswith('> '):
            flow.append(Paragraph(fmt(s[2:]), QUOTE))
        elif s.startswith('---'):
            flow.append(Spacer(1, 4))
            flow.append(HRFlowable(width='100%', thickness=0.6, color=HexColor('#CCCCCC')))
            flow.append(Spacer(1, 4))
        elif re.match(r'^\d+\.\s', s):
            flow.append(Paragraph(fmt(re.sub(r'^\d+\.\s', '', s)), BULLET))
        elif s.startswith('*') and s.endswith('*'):
            flow.append(Paragraph(fmt(s.strip('*')), SMALL))
        else:
            # subtitle line right after the title
            style = SUB if (s.startswith('**Triune') or 'Prepared for Titus' in s) else BODY
            flow.append(Paragraph(fmt(s), style))
    flush_bullets()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(HexColor('#999999'))
        canvas.drawString(inch, 0.5 * inch, 'Triune Solutions — Confidential')
        canvas.drawRightString(letter[0] - inch, 0.5 * inch, f'Page {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate('EXECUTIVE_SUMMARY.pdf', pagesize=letter,
                            leftMargin=inch, rightMargin=inch,
                            topMargin=0.9 * inch, bottomMargin=0.9 * inch,
                            title='HVAC AI Takeoff Tool — Executive Summary',
                            author='Triune Solutions')
    doc.build(flow, onFirstPage=footer, onLaterPages=footer)
    print('wrote EXECUTIVE_SUMMARY.pdf')


if __name__ == '__main__':
    build()
