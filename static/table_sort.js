/* Sort-by-clicking-a-header for every table that shows ALL of its rows (a paginated table is sorted on the server instead — see sort_th in app.py — so a sort
   there covers every page, not just the one on screen).

   Any table with a header row gets it automatically, on page load, after a soft-navigation swap and inside pop-ups: click a header to sort ascending, click again
   for descending. The icon is ⇅ (not sorted) / ▲ / ▼. How a column sorts is read from what it holds: all numbers (₹ and , % stripped; "1,200 Cr" is negative, "Dr" positive)
   sort as numbers, all dates (2026-10-16, 16/10/2026, 16 Oct 2026) as dates, anything else as text without regard to case. Empty cells ("—", blank) always go last.
   A row that spans the table (colspan: a detail line, "no data") stays attached to the row above it.

   Left alone: a table that already sorts on the server (th.sortable), one with a pager beside it, one you type into (text / number / select fields in its rows),
   print / invoice layouts, a table with fewer than two rows, and columns with no title or only an action / # / checkbox.
   Opt out with data-nosort on the table; opt a column out with data-nosort on its header. */
(function () {
  'use strict';
  if (window.__tableSort) return;
  window.__tableSort = true;

  var MONTHS = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };
  var SKIP_HEAD = /^(actions?|#|sr\.?|sl\.?|no\.?|select|rank|)$/i;

  function text(el) { return (el.textContent || '').replace(/\s+/g, ' ').trim(); }
  function isBlank(t) { return t === '' || t === '—' || t === '-' || t === '–' || t === 'N/A' || t === 'n/a'; }

  function parseNumber(t) {
    var s = t.replace(/[₹,%\s]/g, '').replace(/^\((.*)\)$/, '-$1');
    var sign = 1;
    if (/Cr$/i.test(s)) { sign = -1; s = s.replace(/Cr$/i, ''); } else if (/Dr$/i.test(s)) { s = s.replace(/Dr$/i, ''); }
    s = s.replace(/^\+/, '');
    if (!/^-?\d+(\.\d+)?$/.test(s)) return null;
    return sign * parseFloat(s);
  }
  function parseDate(t) {
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(t);
    if (m) return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
    m = /^(\d{1,2})[\/-](\d{1,2})[\/-](\d{4})$/.exec(t);
    if (m) return Date.UTC(+m[3], +m[2] - 1, +m[1]);
    m = /^(\d{1,2}) ([A-Za-z]{3})[a-z]* (\d{4})(?:,? (\d{1,2}):(\d{2}))?/.exec(t);
    if (m && MONTHS[m[2].toLowerCase()] !== undefined) return Date.UTC(+m[3], MONTHS[m[2].toLowerCase()], +m[1], +(m[4] || 0), +(m[5] || 0));
    return null;
  }

  function columnKind(rows, c) {
    var seen = 0, nums = 0, dates = 0;
    rows.forEach(function (r) {
      var cell = r.cells[c]; if (!cell) return;
      var t = text(cell); if (isBlank(t)) return;
      seen++;
      if (parseNumber(t) !== null) nums++;
      if (parseDate(t) !== null) dates++;
    });
    if (!seen) return 'text';
    if (dates === seen) return 'date';
    if (nums === seen) return 'num';
    return 'text';
  }
  function cellValue(cell, kind) {
    var t = cell ? text(cell) : '';
    if (isBlank(t)) return null;
    if (kind === 'num') return parseNumber(t);
    if (kind === 'date') return parseDate(t);
    return t.toLowerCase();
  }

  // A pager belongs to the table it follows. Climb out of the table while the container still holds only THIS table, and look for a pager after it in each
  // container: a pager in a container that also holds other tables is theirs, not this one's.
  function hasPager(table) {
    var el = table;
    while (el.parentElement) {
      var box = el.parentElement;
      if (box.querySelectorAll('table').length > 1 || box === document.body) return false;
      for (var sib = el.nextElementSibling; sib; sib = sib.nextElementSibling) {
        if (sib.matches('.pager-row') || sib.querySelector('.pager-row')) return true;
      }
      el = box;
    }
    return false;
  }

  function eligible(table) {
    if (table.hasAttribute('data-nosort') || table.dataset.sortReady) return false;
    if (table.closest('[data-nosort], .no-print-sort, #invoicePreview, .invoice-preview, .inv-sheet')) return false;
    if (table.querySelector('th.sortable')) return false;                                   // sorted on the server
    if (hasPager(table)) return false;                                                       // paginated: sorted on the server
    if (table.querySelector('tbody input:not([type=checkbox]):not([type=hidden]):not([type=radio]), tbody select, tbody textarea')) return false;
    return true;
  }

  function enhance(table) {
    if (!eligible(table)) return;
    var head = table.tHead && table.tHead.rows[0] ? table.tHead.rows[0] : (table.rows[0] && table.rows[0].querySelector('th') ? table.rows[0] : null);
    if (!head) return;
    var body = table.tBodies[0]; if (!body) return;
    var firstData = head.parentNode === body ? 1 : 0;
    var all = Array.prototype.slice.call(body.rows, firstData);
    var data = all.filter(function (r) { return !r.querySelector('td[colspan], th[colspan]'); });
    if (data.length < 2) return;
    table.dataset.sortReady = '1';
    var state = { col: -1, dir: '' };
    var heads = Array.prototype.slice.call(head.cells);
    var originalOrder = data.slice();

    function groups() {            // each data row with the spanning rows that follow it
      var out = [], cur = null;
      Array.prototype.slice.call(body.rows, firstData).forEach(function (r) {
        if (r.querySelector('td[colspan], th[colspan]')) { if (cur) cur.tail.push(r); else out.push({ row: r, tail: [], fixed: true }); }
        else { cur = { row: r, tail: [] }; out.push(cur); }
      });
      return out;
    }
    function sortBy(c) {
      state.dir = (state.col === c && state.dir === 'asc') ? 'desc' : 'asc';
      state.col = c;
      var kind = columnKind(data, c), gs = groups();
      var movable = gs.filter(function (g) { return !g.fixed; }), fixed = gs.filter(function (g) { return g.fixed; });
      var present = [], blank = [];
      movable.forEach(function (g, i) { var v = cellValue(g.row.cells[c], kind); (v === null ? blank : present).push({ g: g, v: v, i: i }); });
      var sign = state.dir === 'asc' ? 1 : -1;
      present.sort(function (a, b) {
        var r = (kind === 'text') ? String(a.v).localeCompare(String(b.v), undefined, { numeric: true, sensitivity: 'base' }) : (a.v - b.v);
        return r !== 0 ? r * sign : a.i - b.i;
      });
      present.concat(blank).concat(fixed.map(function (g) { return { g: g }; })).forEach(function (x) {
        body.appendChild(x.g.row); x.g.tail.forEach(function (t) { body.appendChild(t); });
      });
      heads.forEach(function (h, i) {
        var ic = h.querySelector('.sort-ic'); if (!ic) return;
        ic.textContent = i === c ? (state.dir === 'asc' ? '▲' : '▼') : '⇅';
        ic.classList.toggle('on', i === c);
        h.setAttribute('aria-sort', i === c ? (state.dir === 'asc' ? 'ascending' : 'descending') : 'none');
      });
    }
    heads.forEach(function (h, c) {
      var label = text(h).replace(/[⇅▲▼]/g, '').trim();
      if (h.hasAttribute('data-nosort') || h.colSpan > 1 || SKIP_HEAD.test(label) || h.querySelector('input, button, select')) return;
      h.classList.add('sort-js');
      h.setAttribute('tabindex', '0'); h.setAttribute('role', 'columnheader'); h.setAttribute('title', 'Sort by ' + label);
      var ic = document.createElement('span'); ic.className = 'sort-ic'; ic.textContent = '⇅';
      h.appendChild(ic);
      h.addEventListener('click', function (e) { if (e.target.closest('a, button, input')) return; sortBy(c); });
      h.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); sortBy(c); } });
    });
  }

  function scan(root) { (root || document).querySelectorAll('table').forEach(enhance); }
  window.enhanceSortableTables = scan;
  window.__tableSortParse = { parseNumber: parseNumber, parseDate: parseDate, columnKind: columnKind, cellValue: cellValue };      // for the test
  var timer = null;
  function later() { clearTimeout(timer); timer = setTimeout(function () { scan(document); }, 60); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', later); else later();
  if (window.MutationObserver) new MutationObserver(later).observe(document.documentElement, { childList: true, subtree: true });
})();
