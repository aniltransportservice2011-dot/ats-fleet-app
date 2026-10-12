"""Standard layout of one execution walkthrough ("flow")."""
from lib import *
import facts as F
import analysis as A

LINE = {f.name: f.lineno for f in F.FUNCS}


def C(t):
    t = str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return f'<font face="Courier">{t}</font>'


def fnl(name):
    return f'{C(name)} ({LINE[name]})' if name in LINE else C(name)


def flow(title, story, start, request, entry, twin, steps, writes, reads, shows, guards=None, level=2):
    """steps: list of str. writes: list of (table, operation, columns/values). reads: list of str. shows: list of str. guards: list of str."""
    st = [H2(title) if level == 2 else H3(title)]
    st.append(P(story))
    rows = [['Where it starts', start], ['What is sent', request], ['Code entry', entry]]
    if twin:
        rows.append(['Phone twin', twin])
    st.append(tbl([['', '']] + rows, [16, 84], header=False, bold_first=True, zebra=False))
    st.append(Spacer(1, 4))
    st.append(P('<b>What happens, step by step</b>', 'body'))
    for i, s in enumerate(steps, 1):
        st.append(Paragraph(safe(s), S['bullet'], bulletText=f'{i}.'))
    if writes:
        st.append(Spacer(1, 3))
        st.append(P('<b>What is saved, and where</b>', 'body'))
        st.append(tbl([['Table', 'Operation', 'Columns / values written']] + [[C(t), o, c] for t, o, c in writes], [20, 13, 67]))
    if reads:
        st.append(Spacer(1, 3))
        st.append(P('<b>What is read</b>', 'body'))
        st += bullets(reads)
    if shows:
        st.append(P('<b>What the user sees next</b>', 'body'))
        st += bullets(shows)
    if guards:
        st.append(P('<b>Protections and edge cases</b>', 'body'))
        st += bullets(guards)
    st.append(Spacer(1, 6))
    return st
