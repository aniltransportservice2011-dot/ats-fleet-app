"""Render diagrams to SVG files + one HTML page (using <img>) so they can be eyeballed in a browser:
   python3 preview.py d_context d_layers ...   (writes $BP_PREVIEW_DIR/preview.html)"""
import sys, os
from reportlab.graphics import renderSVG
import diagrams as D
out = os.environ.get('BP_PREVIEW_DIR', '/tmp')
html = ['<html><head><meta charset="utf-8"></head><body style="margin:0;background:#fff;font-family:sans-serif">']
for name in sys.argv[1:]:
    fig = getattr(D, name)()
    drawing = fig._content[0]
    renderSVG.drawToFile(drawing, f'{out}/{name}.svg')
    html.append('<div style="padding:10px 20px"><div style="font:12px sans-serif;color:#888">' + name + '</div><img src="' + name + '.svg" style="width:' + str(int(drawing.width * 1.6)) + 'px;border:1px solid #eee"></div>')
html.append('</body></html>')
open(f'{out}/preview.html', 'w').write('\n'.join(html))
print('written')
