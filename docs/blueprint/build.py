"""Build the architecture blueprint PDF.   Usage: python3 docs/blueprint/build.py [output.pdf]"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import *
import content_a, content_b, content_c, content_c2, content_d, content_e1, content_e2, content_e3
import content_f, content_g, content_h, content_i

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'ATS_Fleet_App_Architecture_Blueprint.pdf')
OUT = os.path.abspath(OUT)


class SetPart(Flowable):
    """Zero-size marker: sets the running header text for the pages that follow."""
    def __init__(self, name):
        super().__init__(); self.name = name
    def wrap(self, aw, ah):
        return 0, 0
    def draw(self):
        self.canv._doctemplate.current_part = self.name


PARTS = [('Part A \u00b7 The big picture', content_a.part_a), ('Part B \u00b7 Infrastructure and deployment', content_b.part_b),
         ('Part C \u00b7 The back-end', content_c.part_c), ('Part C \u00b7 The back-end', content_c2.part_c2),
         ('Part D \u00b7 The database', content_d.part_d), ('Part E \u00b7 How every feature works', content_e1.part_e1),
         ('Part E \u00b7 How every feature works', content_e2.part_e2), ('Part E \u00b7 How every feature works', content_e3.part_e3),
         ('Part F \u00b7 Execution cards', content_f.part_f), ('Part G \u00b7 The front-end', content_g.part_g),
         ('Part H \u00b7 Quality', content_h.part_h), ('Part I \u00b7 Running and extending', content_i.part_i)]


def story():
    toc = make_toc()
    st = [SetPart('')]
    st += content_a.cover()
    st += content_a.how_to_read()
    st += content_a.toc_page(toc)
    prev = None
    for name, part in PARTS:
        items = part()
        starts_part = any(isinstance(x, PartDivider) for x in items[:5])
        if starts_part and st and isinstance(st[-1], PageBreak):
            st.pop()                       # the divider brings its own page break: avoid a blank page
        if starts_part:
            k = max(n for n, x in enumerate(items[:6]) if isinstance(x, PartDivider))
            items.insert(k + 1, SetPart(name))
        else:
            items.insert(0, SetPart(name))
        st += items
    return st


if __name__ == '__main__':
    t = time.time()
    doc = Doc(OUT, 'ATS Fleet App — Architecture Blueprint')
    doc.multiBuild(story())
    print(f'built {OUT}: {doc.page} pages, {os.path.getsize(OUT)/1e6:.1f} MB, {time.time()-t:.0f}s')
