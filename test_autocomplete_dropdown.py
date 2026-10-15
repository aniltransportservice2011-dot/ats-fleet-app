"""Regression test: every auto-complete drop-down (website + phone app) is drawn by ONE engine (static/autocomplete.js) and comes up directly
under its own field.

What went wrong before: the website only upgraded inputs present on a FULL page load, so after a soft-nav click the browser's own datalist popup
showed instead (different place / look on every screen), while the phone app wrapped each input in a div and drew an absolutely-positioned list that a
bottom sheet's scrolling clipped or pushed off screen. The real-browser geometry (same left edge + width, directly below or above, inside the screen,
tappable, scroll bar) is checked separately in a browser; this guards the code that makes it true.

Usage:  python3 test_autocomplete_dropdown.py
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)
os.chdir(REPO)
ok = fail = 0


def check(cond, msg):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print(f'[FAIL] {msg}')


def read(p):
    return open(os.path.join(REPO, p), encoding='utf-8').read()


js = read('static/autocomplete.js')
# ---- the engine
check("document.addEventListener('focusin'" in js and "document.addEventListener('input'" in js, 'engine: delegated listeners (inputs added later behave the same)')
check('new MutationObserver' in js, 'engine: switches the browser popup off for inputs added later')
check("removeAttribute('list')" in js and "'data-ac-list'" in js, 'engine: the browser datalist popup is switched off (list= moved to data-ac-list)')
check("document.body.appendChild(list)" in js, 'engine: the list lives on <body> (a sheet transform / overflow can never clip or displace it)')
check("r.bottom + 2" in js and 'list.style.left = left' in js and 'list.style.width = width' in js, 'engine: drawn 2px under the field, same left and width')
check("up ? r.top - 2 - list.offsetHeight : r.bottom + 2" in js, 'engine: opens just above the field when there is no room below (placed from its top edge, so a scroll bar cannot shift it)')
check('visualViewport' in js, 'engine: respects an on-screen keyboard')
check("maxHeight" in js and '220' in js, 'engine: capped height (scrolls)')
check("e.key === 'ArrowDown'" in js and "e.key === 'Enter'" in js and "e.key === 'Escape'" in js, 'engine: keyboard (arrows, Enter, Escape)')
check("e.stopPropagation(); close()" in js, 'engine: Escape closes only the list, not the pop-up behind it')
check("dispatchEvent(new Event('input'" in js and "dispatchEvent(new Event('change'" in js, 'engine: picking raises input + change (oninput handlers still run)')
check('quiet = true' in js, 'engine: those events do not pop the list open again')
check('requestAnimationFrame(watch)' in js, 'engine: follows a pop-up that is still sliding open')
check('window.acRegister' in js and 'window.enhanceAutocompleteInputs' in js and 'window.__enhanceAllDatalistInputs' in js, 'engine: public hooks kept (acRegister, enhanceAutocompleteInputs, the phone alias)')

# ---- both bases load it, and neither keeps a second implementation
web = read('templates/base.html')
app = read('templates/app/base_app.html')
trip = read('templates/app/trip_add.html')
check("asset_v('autocomplete.js')" in web and web.rindex("autocomplete.js") > web.rindex('</script>', 0, web.rindex('</body>')) - 400, 'website base loads the shared engine')
check("asset_v('autocomplete.js')" in app, 'phone base loads the shared engine')
check("asset_v('autocomplete.js')" in trip and 'window.acRegister(' in trip, 'phone Add Trip uses the shared engine for Vehicle / Party / Driver / From / To')
check('function enhanceAutocompleteInputs' not in web and "createElement('div')" not in web[web.index('Auto-complete drop-downs'):web.index('Auto-complete drop-downs') + 400], 'website base: no second implementation left')
check('function enhanceDatalistInput' not in app and 'twInitAutocomplete' not in trip, 'phone: no second implementation left')
check('tw-ac-wrap' not in app and "wrap.className" not in app, 'phone base: inputs are no longer wrapped in a div (the wrap shifted layouts)')
check('AcList' not in trip, 'phone Add Trip: no hand-placed suggestion list divs')

# ---- styles
css_web = web[web.index('.autocomplete-list {'):web.index('.autocomplete-list {') + 400]
check('position:fixed' in css_web and 'overflow-y:auto' in css_web and 'z-index:2000' in css_web, 'website css: fixed, scrolls, above every pop-up')
cssapp = read('static/app.css')
blk = cssapp[cssapp.index('.autocomplete-list {'):cssapp.index('.autocomplete-list {') + 500]
check('position: fixed' in blk and 'overflow-y: auto' in blk and 'z-index: 2000' in blk, 'phone css: fixed, scrolls, above sheets (81) and the bottom bar')
check('.autocomplete-item' in cssapp and '.autocomplete-empty' in cssapp, 'phone css: item + hint styles')

# ---- every native list= field stays a real <input list> backed by a datalist that exists on the same page (the engine reads it live)
import glob
bad = []
for path in glob.glob('templates/**/*.html', recursive=True):
    s = read(path)
    for mo in re.finditer(r'<input[^>]*\blist="([A-Za-z0-9_\-]+)"', s):
        lid = mo.group(1)
        if f'id="{lid}"' not in s and 'include' not in s and '_modals' not in path:
            bad.append((path, lid))
check(not bad, f'every <input list> has its datalist in the same template: {bad[:5]}')

print(f'\n{ok} passed, {fail} failed')
sys.exit(1 if fail else 0)
