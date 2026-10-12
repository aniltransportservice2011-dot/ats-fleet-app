"""Layout engine for the architecture blueprint: page template, styles, tables, callouts and a tiny diagram toolkit."""
import datetime
import re
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle, PageBreak,
                                Flowable, KeepTogether, Preformatted, NextPageTemplate, CondPageBreak)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon, Group, Circle, Ellipse, PolyLine
from reportlab.pdfbase.pdfmetrics import stringWidth

# ------------------------------------------------------------------ palette (same family as the app itself)
INK = colors.HexColor('#14213d'); STEEL = colors.HexColor('#5a6b8c'); LINE = colors.HexColor('#dfe3ec'); PAPER = colors.HexColor('#f5f6f9')
ACCENT = colors.HexColor('#c05621'); ACCENT_D = colors.HexColor('#9a4419'); ACCENT_L = colors.HexColor('#fdebdc')
GREEN = colors.HexColor('#1a9c5b'); GREEN_L = colors.HexColor('#e3f5ea'); RED = colors.HexColor('#d6453d'); RED_L = colors.HexColor('#fdecea')
BLUE = colors.HexColor('#2a78d6'); BLUE_L = colors.HexColor('#e7f0fc'); PURPLE = colors.HexColor('#7a5ad6'); PURPLE_L = colors.HexColor('#efeaff')
TEAL = colors.HexColor('#0f9b8e'); TEAL_L = colors.HexColor('#e1f5f2'); AMBER = colors.HexColor('#d99100'); AMBER_L = colors.HexColor('#fdf3e0')
GREY_L = colors.HexColor('#eceef3'); WHITE = colors.white

PAGE_W, PAGE_H = A4
MARGIN = 17 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# ------------------------------------------------------------------ text styles
def _ps(name, **kw):
    base = dict(fontName='Helvetica', fontSize=9.4, leading=13.6, textColor=INK, alignment=TA_LEFT, spaceAfter=5)
    base.update(kw)
    return ParagraphStyle(name, **base)

S = {
    'body': _ps('body'),
    'small': _ps('small', fontSize=8, leading=11, textColor=STEEL),
    'cell': _ps('cell', fontSize=8, leading=10.4, spaceAfter=0),
    'cellb': _ps('cellb', fontSize=8, leading=10.4, spaceAfter=0, fontName='Helvetica-Bold'),
    'cellh': _ps('cellh', fontSize=8, leading=10.4, spaceAfter=0, fontName='Helvetica-Bold', textColor=WHITE),
    'cellm': _ps('cellm', fontSize=7.4, leading=9.4, spaceAfter=0, fontName='Courier'),
    'h1': _ps('h1', fontName='Helvetica-Bold', fontSize=21, leading=25, spaceBefore=4, spaceAfter=10, textColor=INK),
    'h2': _ps('h2', fontName='Helvetica-Bold', fontSize=14.5, leading=18, spaceBefore=12, spaceAfter=6, textColor=ACCENT_D),
    'h3': _ps('h3', fontName='Helvetica-Bold', fontSize=11, leading=14, spaceBefore=8, spaceAfter=3, textColor=INK),
    'bullet': _ps('bullet', leftIndent=13, bulletIndent=3, spaceAfter=2.5),
    'lead': _ps('lead', fontSize=11, leading=16, textColor=STEEL, spaceAfter=8),
    'code': ParagraphStyle('code', fontName='Courier', fontSize=7.6, leading=9.6, textColor=INK, backColor=PAPER, borderPadding=(5, 5, 5, 5), spaceAfter=8, leftIndent=4, rightIndent=4),
    'toc1': _ps('toc1', fontName='Helvetica-Bold', fontSize=10.5, leading=15, spaceBefore=6, textColor=INK),
    'toc2': _ps('toc2', fontSize=9, leading=12.5, leftIndent=14, textColor=INK, spaceAfter=0),
    'caption': _ps('caption', fontSize=8, leading=10.5, textColor=STEEL, alignment=TA_CENTER, spaceAfter=10),
}

# ------------------------------------------------------------------ flowable helpers
_ALLOWED_TAGS = r'(?:b|i|u|font|br|a|super|sub|strike)'
def safe(t):
    """Make free text safe for ReportLab's mini-markup: only a small set of tags is kept, any other '<' and any bare '&' is escaped."""
    t = str(t)
    t = re.sub(r'<(?!/?' + _ALLOWED_TAGS + r'(?:\s|/|>))', '&lt;', t)
    t = re.sub(r'&(?!(?:amp|lt|gt|quot|nbsp|#\d+|#x[0-9a-fA-F]+);)', '&amp;', t)
    return t

def esc(t):
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def P(text, style='body'):
    return Paragraph(safe(text), S[style] if isinstance(style, str) else style)

def bullets(items, style='bullet'):
    return [Paragraph(t, S[style], bulletText='•') for t in items]

def code(text):
    return Preformatted(text.strip('\n'), S['code'])

class Heading(Paragraph):
    """Paragraph that also tells the table-of-contents about itself."""
    def __init__(self, text, style, level):
        text = safe(text)
        super().__init__(text, style)
        self.toc_level = level
        self.toc_text = text

def H1(text): return Heading(text, S['h1'], 0)
def H2(text): return Heading(text, S['h2'], 1)
def H3(text): return Paragraph(safe(text), S['h3'])

def tbl(rows, widths, header=True, zebra=True, mono_cols=(), bold_first=False, font=None, valign='TOP'):
    """rows: list of lists of str (inline markup allowed). widths: relative or absolute (scaled to content width)."""
    tot = float(sum(widths))
    widths = [CONTENT_W * w / tot for w in widths] if tot <= 100 or tot < CONTENT_W else widths
    out = []
    for ri, r in enumerate(rows):
        row = []
        for ci, c in enumerate(r):
            if header and ri == 0:
                st = S['cellh']
            elif ci in mono_cols:
                st = S['cellm']
            elif bold_first and ci == 0:
                st = S['cellb']
            else:
                st = S['cell']
            row.append(Paragraph(safe(c if isinstance(c, str) else str(c)), st))
        out.append(row)
    t = Table(out, colWidths=widths, repeatRows=1 if header else 0)
    style = [('VALIGN', (0, 0), (-1, -1), valign), ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
             ('TOPPADDING', (0, 0), (-1, -1), 3.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.2),
             ('LINEBELOW', (0, 0), (-1, -1), 0.4, LINE), ('BOX', (0, 0), (-1, -1), 0.6, LINE)]
    if header:
        style += [('BACKGROUND', (0, 0), (-1, 0), INK)]
    if zebra:
        for i in range(1 if header else 0, len(out)):
            if i % 2 == 0:
                style.append(('BACKGROUND', (0, i), (-1, i), PAPER))
    t.setStyle(TableStyle(style))
    return t

def callout(kind, title, text):
    pal = {'note': (BLUE_L, BLUE), 'tip': (GREEN_L, GREEN), 'warn': (AMBER_L, AMBER), 'danger': (RED_L, RED), 'plain': (ACCENT_L, ACCENT)}[kind]
    inner = [Paragraph(f'<b>{safe(title)}</b>', _ps('ct', fontSize=9.2, textColor=pal[1], spaceAfter=2)), Paragraph(safe(text), _ps('cb', fontSize=8.8, leading=12.4, spaceAfter=0))]
    t = Table([[inner]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), pal[0]), ('LINEBEFORE', (0, 0), (0, -1), 3, pal[1]), ('LEFTPADDING', (0, 0), (-1, -1), 9),
                           ('RIGHTPADDING', (0, 0), (-1, -1), 9), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 7)]))
    return KeepTogether([t, Spacer(1, 7)])

def stat_row(items):
    """items: [(big, small)] -> a row of metric tiles."""
    n = len(items)
    w = CONTENT_W / n
    cells = [[Paragraph(f'<font size="17" color="#c05621"><b>{a}</b></font><br/><font size="7.6" color="#5a6b8c">{b}</font>', _ps('st', alignment=TA_CENTER, leading=17, spaceAfter=0))] for a, b in items]
    t = Table([[c[0] for c in cells]], colWidths=[w] * n)
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), PAPER), ('BOX', (0, 0), (-1, -1), 0.6, LINE), ('INNERGRID', (0, 0), (-1, -1), 0.6, WHITE),
                           ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
    return [t, Spacer(1, 8)]

class PartDivider(Flowable):
    """Full-page coloured divider that opens each Part of the document."""
    def __init__(self, num, title, subtitle, items):
        super().__init__()
        self.num, self.title, self.subtitle, self.items = num, title, subtitle, items
    def wrap(self, aw, ah):
        self.w, self.h = aw, ah
        return aw, ah - 2
    def draw(self):
        c = self.canv
        c.saveState()
        top = self.h
        c.setFillColor(INK)
        c.rect(-MARGIN, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        c.setFillColor(ACCENT)
        c.rect(-MARGIN, top - 250, 14, 230, stroke=0, fill=1)
        c.setFont('Helvetica-Bold', 54)
        c.drawString(10, top - 120, self.num)
        c.setFillColor(WHITE)
        c.setFont('Helvetica-Bold', 29)
        y = top - 175
        for ln in wrap_text(self.title, 'Helvetica-Bold', 29, self.w - 20):
            c.drawString(10, y, ln); y -= 34
        c.setFont('Helvetica', 12.5)
        c.setFillColor(colors.HexColor('#c9d2e6'))
        y -= 6
        for ln in wrap_text(self.subtitle, 'Helvetica', 12.5, self.w - 40):
            c.drawString(10, y, ln); y -= 17
        y -= 24
        c.setFont('Helvetica-Bold', 9.5); c.setFillColor(ACCENT_L)
        c.drawString(10, y, 'IN THIS PART'); y -= 18
        c.setFont('Helvetica', 10.5); c.setFillColor(WHITE)
        for it in self.items:
            c.drawString(22, y, '\u2022  ' + it); y -= 16
        c.restoreState()



class CoverPage(Flowable):
    def __init__(self, title, subtitle, lines):
        super().__init__()
        self.title, self.subtitle, self.lines = title, subtitle, lines
    def wrap(self, aw, ah):
        self.w, self.h = aw, ah
        return aw, ah - 2
    def draw(self):
        c = self.canv
        c.saveState()
        top = self.h
        c.setFillColor(INK); c.rect(-MARGIN, 0, PAGE_W, PAGE_H, stroke=0, fill=1)
        c.setFillColor(ACCENT); c.rect(-MARGIN, top - 330, PAGE_W, 5, stroke=0, fill=1)
        c.setFont('Helvetica-Bold', 11); c.setFillColor(ACCENT_L)
        c.drawString(0, top - 90, 'ANIL TRANSPORT SERVICE  \u00B7  FLEET MANAGEMENT')
        c.setFillColor(WHITE); c.setFont('Helvetica-Bold', 41)
        y = top - 160
        for ln in wrap_text(self.title, 'Helvetica-Bold', 41, self.w):
            c.drawString(0, y, ln); y -= 48
        c.setFont('Helvetica', 15); c.setFillColor(colors.HexColor('#c9d2e6'))
        y -= 8
        for ln in wrap_text(self.subtitle, 'Helvetica', 15, self.w - 20):
            c.drawString(0, y, ln); y -= 21
        c.setFont('Helvetica', 10.5); c.setFillColor(colors.HexColor('#9fb0d0'))
        y = top - 372
        for ln in self.lines:
            c.drawString(0, y, ln); y -= 16
        # decorative route line
        c.setStrokeColor(ACCENT); c.setLineWidth(2.2); c.setDash(7, 6)
        c.line(0, 118, self.w, 118)
        c.setDash()
        c.setFillColor(ACCENT); c.circle(0, 118, 5, stroke=0, fill=1); c.circle(self.w, 118, 5, stroke=0, fill=1)
        c.setFillColor(WHITE); c.setFont('Helvetica-Bold', 9.5)
        c.drawString(0, 98, 'From a trip booked on a phone  \u2192  to a balance, an invoice and a decision.')
        c.restoreState()

# ------------------------------------------------------------------ document with TOC
class Doc(BaseDocTemplate):
    def __init__(self, path, title):
        super().__init__(path, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=22 * mm, bottomMargin=18 * mm,
                         title=title, author='Anil Transport Service — fleet app', subject='Architecture blueprint')
        self.doc_title = title
        self.current_part = ''
        frame = Frame(MARGIN, 18 * mm, CONTENT_W, PAGE_H - 40 * mm, id='f', leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        full = Frame(MARGIN, 0, CONTENT_W, PAGE_H, id='full', leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id='body', frames=[frame], onPage=self._decor),
                               PageTemplate(id='divider', frames=[full], onPage=lambda c, d: None),
                               PageTemplate(id='cover', frames=[full], onPage=lambda c, d: None)])
    def _decor(self, canv, doc):
        canv.saveState()
        canv.setStrokeColor(LINE); canv.setLineWidth(0.6)
        canv.line(MARGIN, PAGE_H - 15 * mm, PAGE_W - MARGIN, PAGE_H - 15 * mm)
        canv.setFont('Helvetica-Bold', 7.8); canv.setFillColor(ACCENT)
        canv.drawString(MARGIN, PAGE_H - 12.4 * mm, 'ATS FLEET APP · ARCHITECTURE BLUEPRINT')
        canv.setFont('Helvetica', 7.8); canv.setFillColor(STEEL)
        canv.drawRightString(PAGE_W - MARGIN, PAGE_H - 12.4 * mm, getattr(self, 'current_part', ''))
        canv.line(MARGIN, 14 * mm, PAGE_W - MARGIN, 14 * mm)
        canv.drawString(MARGIN, 9.5 * mm, 'Anil Transport Service · generated ' + datetime.date.today().strftime('%d %b %Y'))
        canv.drawRightString(PAGE_W - MARGIN, 9.5 * mm, f'Page {doc.page}')
        canv.restoreState()
    def afterFlowable(self, fl):
        if isinstance(fl, Heading):
            key = f'h{self.seq.nextf("heading")}'
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(fl.toc_text, key, level=fl.toc_level, closed=fl.toc_level > 0)
            self.notify('TOCEntry', (fl.toc_level, fl.toc_text, self.page, key))

def make_toc():
    toc = TableOfContents()
    toc.levelStyles = [S['toc1'], S['toc2']]
    toc.dotsMinLevel = 0
    return toc

# ------------------------------------------------------------------ diagram toolkit
def wrap_text(text, font, size, maxw):
    words, lines, cur = str(text).split(), [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if stringWidth(t, font, size) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines or ['']

class Diagram:
    def __init__(self, w=CONTENT_W, h=300):
        self.w, self.h = w, h
        self.d = Drawing(w, h)

    def box(self, x, y, w, h, title, sub=None, fill=PAPER, stroke=STEEL, tcolor=INK, scolor=STEEL, size=8.4, rx=5, bold=True, align='center'):
        self.d.add(Rect(x, y, w, h, rx=rx, ry=rx, fillColor=fill, strokeColor=stroke, strokeWidth=0.9))
        self._text_block(x, y, w, h, title, sub, tcolor, scolor, size, bold, align)
        return (x, y, w, h)

    def _text_block(self, x, y, w, h, title, sub, tcolor, scolor, size, bold, align):
        fs = size
        tf = 'Helvetica-Bold' if bold else 'Helvetica'
        while True:
            tl = wrap_text(title, tf, fs, w - 8)
            sl = wrap_text(sub, 'Helvetica', fs - 1.3, w - 8) if sub else []
            need = len(tl) * (fs + 2) + len(sl) * (fs + 0.7) + (3 if sl else 0)
            widest = max([stringWidth(l, tf, fs) for l in tl] + [stringWidth(l, 'Helvetica', fs - 1.3) for l in sl] + [0])
            if (need <= h - 4 and widest <= w - 6) or fs <= 5.6:
                break
            fs -= 0.4
        cy = y + h / 2 + need / 2 - fs
        for ln in tl:
            self._str(x, cy, w, ln, tf, fs, tcolor, align); cy -= fs + 2
        if sl:
            cy -= 1
            for ln in sl:
                self._str(x, cy, w, ln, 'Helvetica', fs - 1.3, scolor, align); cy -= fs + 0.7

    def _str(self, x, y, w, text, font, size, color, align):
        if align == 'center':
            self.d.add(String(x + w / 2, y, text, fontName=font, fontSize=size, fillColor=color, textAnchor='middle'))
        else:
            self.d.add(String(x + 6, y, text, fontName=font, fontSize=size, fillColor=color))

    def text(self, x, y, text, size=8, color=INK, bold=False, anchor='start'):
        self.d.add(String(x, y, text, fontName='Helvetica-Bold' if bold else 'Helvetica', fontSize=size, fillColor=color, textAnchor=anchor))

    def para(self, x, y, w, text, size=7.8, color=STEEL, bold=False):
        f = 'Helvetica-Bold' if bold else 'Helvetica'
        for ln in wrap_text(text, f, size, w):
            self.text(x, y, ln, size, color, bold); y -= size + 2.2
        return y

    def lane(self, x, y, w, h, title, fill=PAPER, color=STEEL):
        self.d.add(Rect(x, y, w, h, rx=7, ry=7, fillColor=fill, strokeColor=color, strokeWidth=0.8, strokeDashArray=[3, 2]))
        self.text(x + 8, y + h - 12, title, 7.8, color, True)

    def cyl(self, x, y, w, h, title, sub=None, fill=BLUE_L, stroke=BLUE):
        ry = 7
        self.d.add(Rect(x, y + ry, w, h - 2 * ry, fillColor=fill, strokeColor=None))
        self.d.add(Line(x, y + ry, x, y + h - ry, strokeColor=stroke, strokeWidth=0.9)); self.d.add(Line(x + w, y + ry, x + w, y + h - ry, strokeColor=stroke, strokeWidth=0.9))
        self.d.add(Ellipse(x + w / 2, y + ry, w / 2, ry, fillColor=fill, strokeColor=stroke, strokeWidth=0.9))
        self.d.add(Ellipse(x + w / 2, y + h - ry, w / 2, ry, fillColor=fill, strokeColor=stroke, strokeWidth=0.9))
        self._text_block(x, y, w, h, title, sub, INK, STEEL, 8.4, True, 'center')

    def arrow(self, pts, color=STEEL, label=None, lpos=0.5, dashed=False, width=1.1, loff=(0, 5), both=False):
        """pts: [(x,y),...] polyline; arrow head at the end (and start if both)."""
        flat = [c for p in pts for c in p]
        kw = dict(strokeColor=color, strokeWidth=width)
        if dashed:
            kw['strokeDashArray'] = [3, 2]
        self.d.add(PolyLine(flat, **kw))
        self._head(pts[-2], pts[-1], color)
        if both:
            self._head(pts[1], pts[0], color)
        if label:
            seg = max(range(len(pts) - 1), key=lambda i: abs(pts[i + 1][0] - pts[i][0]) + abs(pts[i + 1][1] - pts[i][1]))
            (x1, y1), (x2, y2) = pts[seg], pts[seg + 1]
            mx, my = x1 + (x2 - x1) * lpos + loff[0], y1 + (y2 - y1) * lpos + loff[1]
            tw = stringWidth(label, 'Helvetica', 6.8) + 4
            self.d.add(Rect(mx - tw / 2, my - 2, tw, 9, fillColor=WHITE, strokeColor=None))
            self.d.add(String(mx, my, label, fontName='Helvetica', fontSize=6.8, fillColor=color, textAnchor='middle'))

    def _head(self, p0, p1, color, size=4.6):
        import math
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        a1, a2 = ang + math.radians(155), ang - math.radians(155)
        self.d.add(Polygon([p1[0], p1[1], p1[0] + size * math.cos(a1), p1[1] + size * math.sin(a1), p1[0] + size * math.cos(a2), p1[1] + size * math.sin(a2)],
                           fillColor=color, strokeColor=color, strokeWidth=0.5))

    def pill(self, x, y, text, fill=ACCENT, color=WHITE, size=7):
        tw = stringWidth(text, 'Helvetica-Bold', size) + 10
        self.d.add(Rect(x, y, tw, size + 6, rx=(size + 6) / 2, ry=(size + 6) / 2, fillColor=fill, strokeColor=None))
        self.d.add(String(x + tw / 2, y + 3.4, text, fontName='Helvetica-Bold', fontSize=size, fillColor=color, textAnchor='middle'))
        return tw

    def build(self):
        return self.d

def figure(diagram, caption):
    return KeepTogether([diagram.build(), Paragraph(caption, S['caption'])])
