/* Auto-complete drop-downs — ONE engine for the website and the phone app.

   Any <input list="some-datalist"> (and any input registered with acRegister(input, options)) gets the same drop-down:
     - one shared list element, drawn directly UNDER the input that has the focus: same left edge, same width, 2px below it;
     - when the screen has no room below (an input near the bottom of a pop-up / sheet / the keyboard is up) it opens just ABOVE instead;
     - at most ~220px tall with its own scroll bar, never clipped by a pop-up's own scrolling, and above every pop-up / sheet / bottom bar;
     - follows its input while the page scrolls or a pop-up is still sliding open, and hides while the input is scrolled out of sight;
     - Arrow Up/Down + Enter to pick, Escape closes only the list (not the pop-up behind it), Tab leaves;
     - picking a value raises the same input + change events the browser's own list would, so a field's oninput handler still runs.

   It is driven by delegated listeners on the document, so inputs that appear later — a page reached by a soft-nav click, a pop-up, a row added
   by a script — behave exactly like the ones present at load. The browser's own datalist popup (different place and look on every screen) is
   switched off by moving list="..." to data-ac-list the moment an input is seen.

   A page can set  window.AC_HINT = 'text'  to show that line when the list has options but none match what was typed.
   Public: window.acRegister(input, optionsOrFn), window.enhanceAutocompleteInputs(root) (kept for older callers). */
(function () {
  'use strict';
  if (window.__acLoaded) return;
  window.__acLoaded = true;

  var list = null, input = null, items = [], active = -1, raf = 0, quiet = false;

  function el() {
    if (list) return list;
    list = document.createElement('div');
    list.className = 'autocomplete-list';
    list.setAttribute('role', 'listbox');
    list.style.display = 'none';
    document.body.appendChild(list);
    // mousedown (not click): the input must keep the focus, or its blur would close the list before the pick registers
    list.addEventListener('mousedown', function (e) {
      e.preventDefault();
      var item = e.target.closest('.autocomplete-item');
      if (item) pick(item.textContent);
    });
    list.addEventListener('mousemove', function (e) {
      var item = e.target.closest('.autocomplete-item');
      if (item) highlight(items.indexOf(item), false);
    });
    return list;
  }

  function adopt(inp) {
    if (!inp || inp.nodeType !== 1 || inp.tagName !== 'INPUT') return false;
    var id = inp.getAttribute('list');
    if (id) {
      inp.setAttribute('data-ac-list', id);
      inp.removeAttribute('list');             // no browser popup on top of ours
      inp.setAttribute('autocomplete', 'off');
      inp.setAttribute('data-ac-enhanced', '1');
    }
    return !!(inp.getAttribute('data-ac-list') || inp.__acOptions);
  }

  function optionsOf(inp) {
    if (inp.__acOptions) return (typeof inp.__acOptions === 'function' ? inp.__acOptions() : inp.__acOptions).filter(Boolean);
    var dl = document.getElementById(inp.getAttribute('data-ac-list'));            // read live: some lists are refilled by the page
    if (!dl) return [];
    return Array.prototype.map.call(dl.querySelectorAll('option'), function (o) { return o.value; }).filter(Boolean);
  }

  function close() {
    if (list) { list.style.display = 'none'; list.innerHTML = ''; }
    items = []; active = -1; input = null;
    if (raf) { cancelAnimationFrame(raf); raf = 0; }
  }

  // The part of the screen the input can actually be seen in: the viewport (minus an on-screen keyboard), narrowed by every ancestor that clips
  // or scrolls it (a bottom sheet's scrolling form, a pop-up body).
  function visibleArea(inp) {
    var vv = window.visualViewport;
    var area = { top: vv ? vv.offsetTop : 0, bottom: vv ? vv.offsetTop + vv.height : window.innerHeight, left: 0, right: window.innerWidth };
    for (var p = inp.parentElement; p && p !== document.body; p = p.parentElement) {
      var cs = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(cs.overflowY + cs.overflowX)) {
        var r = p.getBoundingClientRect();
        area.top = Math.max(area.top, r.top); area.bottom = Math.min(area.bottom, r.bottom);
        area.left = Math.max(area.left, r.left); area.right = Math.min(area.right, r.right);
      }
    }
    return area;
  }

  function place() {
    if (!input || !list) return;
    if (!input.isConnected) { close(); return; }
    var r = input.getBoundingClientRect();
    var vis = visibleArea(input);
    var vv = window.visualViewport;
    var screenTop = vv ? vv.offsetTop : 0, screenBottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
    // input scrolled out of its own pop-up / the screen: nothing to anchor to
    if (!r.width || !r.height || r.bottom <= vis.top || r.top >= vis.bottom) { list.style.visibility = 'hidden'; return; }
    var below = screenBottom - r.bottom - 8, above = r.top - screenTop - 8, want = Math.min(220, list.scrollHeight + 2);
    var up = below < Math.min(want, 140) && above > below;                         // not enough room underneath: open upward instead
    var room = Math.max(90, up ? above : below);
    var width = Math.min(r.width, window.innerWidth - 8);
    var left = Math.min(Math.max(r.left, 4), Math.max(4, window.innerWidth - width - 4));
    list.style.visibility = 'visible';
    list.style.left = left + 'px';
    list.style.width = width + 'px';
    list.style.maxHeight = Math.min(220, room) + 'px';
    list.style.bottom = 'auto';
    // placed from the top edge in both cases (not from the screen's bottom edge, which differs from innerHeight by a horizontal scroll bar)
    list.style.top = (up ? r.top - 2 - list.offsetHeight : r.bottom + 2) + 'px';
  }

  function watch() {            // a pop-up that is still sliding open moves its inputs — follow them until the list closes
    place();
    raf = input ? requestAnimationFrame(watch) : 0;
  }

  function highlight(i, scroll) {
    if (active >= 0 && items[active]) items[active].classList.remove('highlighted');
    active = i;
    if (i >= 0 && items[i]) {
      items[i].classList.add('highlighted');
      if (scroll) items[i].scrollIntoView({ block: 'nearest' });
    }
  }

  function show(inp) {
    if (!adopt(inp) || inp.readOnly || inp.disabled) return;
    var val = inp.value.trim().toLowerCase();
    var all = optionsOf(inp);
    var matches = (val ? all.filter(function (o) { return o.toLowerCase().indexOf(val) !== -1; }) : all).slice(0, 300);
    var box = el();
    // exactly what is typed and nothing else to offer → nothing to show
    if (matches.length === 1 && matches[0].toLowerCase() === val) { close(); return; }
    if (!matches.length) {
      if (!(window.AC_HINT && all.length)) { close(); return; }
      box.innerHTML = '';
      var hint = document.createElement('div');
      hint.className = 'autocomplete-empty';
      hint.textContent = window.AC_HINT;
      box.appendChild(hint);
      items = [];
    } else {
      box.innerHTML = '';
      items = matches.map(function (m) {
        var item = document.createElement('div');
        item.className = 'autocomplete-item';
        item.setAttribute('role', 'option');
        item.textContent = m;
        box.appendChild(item);
        return item;
      });
    }
    input = inp; active = -1;
    box.style.display = 'block';
    place();
    if (!raf) raf = requestAnimationFrame(watch);
  }

  function pick(value) {
    var inp = input;
    if (!inp) return;
    close();
    inp.value = value;
    // the same events the browser's own list would raise, so a field's oninput / change handler (vehicle type, paid amount, …) still runs
    quiet = true;                              // those events must not pop the list open again
    try {
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      inp.dispatchEvent(new Event('change', { bubbles: true }));
    } finally { quiet = false; }
  }

  document.addEventListener('focusin', function (e) { if (e.target.tagName === 'INPUT' && adopt(e.target)) show(e.target); });
  document.addEventListener('click', function (e) {
    var t = e.target;
    if (t.tagName === 'INPUT' && adopt(t)) { if (input !== t) show(t); }
    else if (list && !list.contains(t)) close();
  });
  document.addEventListener('input', function (e) {
    var t = e.target;
    if (!quiet && t.tagName === 'INPUT' && (t.getAttribute('data-ac-list') || t.__acOptions) && document.activeElement === t) show(t);
  });
  document.addEventListener('focusout', function (e) {
    if (e.target === input) setTimeout(function () { if (input === e.target && document.activeElement !== e.target) close(); }, 120);
  });
  document.addEventListener('keydown', function (e) {
    if (!input || e.target !== input) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!items.length) return;
      e.preventDefault();
      var n = items.length;
      highlight(e.key === 'ArrowDown' ? (active + 1) % n : (active <= 0 ? n - 1 : active - 1), true);
    } else if (e.key === 'Enter' && active >= 0) {
      e.preventDefault(); pick(items[active].textContent);
    } else if (e.key === 'Escape') {
      e.preventDefault(); e.stopPropagation(); close();         // closes only the list, not the pop-up that holds the input
    } else if (e.key === 'Tab') {
      close();
    }
  }, true);
  window.addEventListener('resize', place);
  window.addEventListener('scroll', function (e) { if (input && !(list && list.contains(e.target))) place(); }, true);
  if (window.visualViewport) { window.visualViewport.addEventListener('resize', place); window.visualViewport.addEventListener('scroll', place); }
  window.addEventListener('popstate', close);
  window.addEventListener('pagehide', close);

  function enhance(root) {
    (root && root.querySelectorAll ? root : document).querySelectorAll('input[list]').forEach(adopt);
  }
  window.enhanceAutocompleteInputs = enhance;
  window.__enhanceAllDatalistInputs = enhance;                  // the phone app's older name
  window.acRegister = function (inp, options) {                 // an input whose choices come from a script, not a <datalist>
    if (!inp) return;
    inp.__acOptions = options;
    inp.setAttribute('autocomplete', 'off');
    inp.setAttribute('data-ac-enhanced', '1');
  };

  function start() {
    enhance();
    // switch the browser's own popup off as early as possible for anything added later
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        for (var j = 0; j < muts[i].addedNodes.length; j++) {
          var n = muts[i].addedNodes[j];
          if (n.nodeType !== 1) continue;
          if (n.tagName === 'INPUT') adopt(n); else if (n.querySelector && n.querySelector('input[list]')) enhance(n);
        }
      }
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
})();
