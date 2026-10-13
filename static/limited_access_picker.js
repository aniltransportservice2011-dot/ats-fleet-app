/* LAPicker — choose which screens a Limited Access user may open, and View or Edit for each.
   Writes hidden inputs  name="modules"  value="Trips:e" / "Ledger:v"  (what add_user / edit_user expect).
   When Trips is View-only it also offers  name="hide_trip_amounts" value="1"  — stars instead of rates, bills and payments on Trips.
   When Salaries is View-only it offers whose salary that user may see: their OWN salary is always shown (the app finds it by matching the login's
   mobile number to an employee's mobile number; a live hint says whether it matches), plus "Everyone"  (name="salary_everyone", value "1")  or any
   ticked employees  (name="salary_ids", value = employee id, several). The employee list comes from  window.LA_EMPLOYEES = [{id, name, type, mobile}]. */
(function () {
  var MODS = [
    { k: 'Dashboard', g: 'Overview', ic: '\u{1F3E0}', card: 'Today', v: '12 trips' },
    { k: 'Trips', g: 'Operations', ic: '\u{1F4CD}', card: 'Trips', v: '472' },
    { k: 'Vehicles', g: 'Operations', ic: '\u{1F69A}', card: 'Vehicles', v: '60' },
    { k: 'Maintenance', g: 'Operations', ic: '\u{1F527}', card: 'Service due', v: '6' },
    { k: 'Ledger', g: 'Finance', ic: '\u{1F4D2}', card: 'Receivable', v: '₹ 8.4 L' },
    { k: 'Invoices', g: 'Finance', ic: '\u{1F9FE}', card: 'Invoices', v: '38' },
    { k: 'Expenses', g: 'Finance', ic: '\u{1F45B}', card: 'Expenses', v: '₹ 1.2 L' },
    { k: 'Salaries', g: 'People', ic: '\u{1F465}', card: 'Payroll', v: '₹ 3.1 L' },
    { k: 'Reports', g: 'Analysis', ic: '\u{1F4CA}', card: 'Profit', v: '₹ 5.6 L' }
  ];
  var PRESETS = {
    'Accountant': 'Dashboard:v,Ledger:e,Invoices:e,Expenses:e,Reports:v',
    'Operations': 'Dashboard:v,Trips:e,Vehicles:v,Maintenance:e',
    'Fleet and service': 'Trips:v,Vehicles:e,Maintenance:e'
  };
  function parse(s) {
    var o = {};
    (s || '').split(',').forEach(function (p) {
      p = p.trim(); if (!p) return;
      var a = p.split(':'), n = a[0].trim(); if (n === 'Payments') n = 'Ledger';
      if (MODS.some(function (m) { return m.k === n; })) o[n] = (a[1] || '').trim().toLowerCase().charAt(0) === 'v' ? 'v' : 'e';
    });
    return o;
  }
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function mount(el, opts) {
    opts = opts || {};
    var st = parse(opts.initial), preset = '';
    var ex = { hide: !!opts.hide, salary_all: false, salary_ids: [] };
    function takeExtras(x) {
      if (x === undefined || x === null) return;
      if (typeof x === 'boolean') { ex.hide = x; return; }
      ['hide', 'salary_all'].forEach(function (k) { if (k in x) ex[k] = !!x[k]; });
      if ('salary_ids' in x) ex.salary_ids = (x.salary_ids || []).map(String);
    }
    takeExtras(opts.extras);
    el.classList.add('la-picker');
    if (opts.preview) el.classList.add('la-wide');
    function matchPreset() {
      preset = 'Custom';
      Object.keys(PRESETS).forEach(function (p) { if (JSON.stringify(sorted(parse(PRESETS[p]))) === JSON.stringify(sorted(st))) preset = p; });
    }
    function sorted(o) { return Object.keys(o).sort().map(function (k) { return k + ':' + o[k]; }); }
    function render() {
      matchPreset();
      var h = '<div class="la-presets">' + Object.keys(PRESETS).concat(['Custom']).map(function (p) {
        return '<button type="button" class="la-chip' + (p === preset ? ' on' : '') + '" data-p="' + esc(p) + '">' + esc(p) + '</button>';
      }).join('') + '</div><div class="la-body"><div class="la-list">';
      var g = '';
      MODS.forEach(function (m) {
        if (m.g !== g) { g = m.g; h += '<div class="la-grp">' + g + '</div>'; }
        var s = st[m.k];
        h += '<div class="la-tile' + (s ? ' on' : '') + '"><span class="la-ic" aria-hidden="true">' + m.ic + '</span><span class="la-nm">' + m.k + '</span>' +
          (s ? '<span class="la-seg"><button type="button" data-k="' + m.k + '" data-m="v" class="' + (s === 'v' ? 'on' : '') + '">View</button><button type="button" data-k="' + m.k + '" data-m="e" class="' + (s === 'e' ? 'on' : '') + '">Edit</button></span>' : '') +
          '<button type="button" class="la-sw' + (s ? ' on' : '') + '" data-t="' + m.k + '" aria-label="Allow ' + m.k + '"></button></div>';
        if (m.k === 'Trips' && s === 'v') {      // right under the Trips tile, where it is obvious
          h += '<label class="la-hide"><input type="checkbox" name="hide_trip_amounts" value="1"' + (ex.hide ? ' checked' : '') + '><span><b>Hide amounts on Trips</b><br>Shows \u2605\u2605\u2605\u2605 instead of the rate, trip bill, charges, deductions and payments. Fuel, driver advance, route, party and tonnage stay visible.</span></label>';
        }
        if (m.k === 'Salaries' && s === 'v') {   // right under the Salaries tile: which salaries this user sees
          var emps = (window.LA_EMPLOYEES || []);
          h += '<div class="la-hide la-sal"><span><b>Which salaries can this user see?</b><br>Their own salary is always shown (matched by mobile number). Anyone else only if you tick them.</span>' +
            '<div class="la-match" role="status"></div>' +
            '<div class="la-also">Also show the salary of:</div>' +
            '<div class="la-ms">' +
              '<div class="la-ms-field" tabindex="0" role="button" aria-expanded="false" aria-label="Choose employees"><span class="la-ms-chips"></span><span class="la-ms-caret" aria-hidden="true">\u25BE</span></div>' +
              '<div class="la-ms-panel" hidden>' +
                '<input type="search" class="la-ms-search" placeholder="Search employees\u2026" autocomplete="off" aria-label="Search employees">' +
                '<div class="la-ms-bar"><button type="button" data-a="all">Select all</button><button type="button" data-a="none">Clear</button><span class="la-ms-count"></span></div>' +
                '<label class="la-opt la-ms-every"><input type="checkbox" name="salary_everyone" value="1"' + (ex.salary_all ? ' checked' : '') + '><span>Everyone <em>(also people added later)</em></span></label>' +
                '<div class="la-ms-list">' + (emps.length ? emps.map(function (e) {
                  return '<label class="la-opt" data-n="' + esc((e.name + ' ' + (e.type || '')).toLowerCase()) + '"><input type="checkbox" name="salary_ids" value="' + e.id + '"' + ((ex.salary_all || ex.salary_ids.indexOf(String(e.id)) >= 0) ? ' checked' : '') + (ex.salary_all ? ' disabled' : '') + '><span>' + esc(e.name) + (e.type ? ' <em>' + esc(e.type) + '</em>' : '') + '</span></label>';
                }).join('') : '<em class="la-ms-none">No employees yet.</em>') + '<div class="la-ms-empty" hidden>No match.</div></div>' +
              '</div>' +
            '</div></div>';
        }
      });
      var on = MODS.filter(function (m) { return st[m.k]; }), nv = on.filter(function (m) { return st[m.k] === 'v'; }).length;
      h += '<div class="la-sum">' + (on.length ? on.length + ' screen' + (on.length > 1 ? 's' : '') + (nv ? ' (' + nv + ' view only)' : '') + ' · opens on ' + on[0].k : 'No screens chosen yet — pick at least one.') + '</div>' +
        '<div class="la-note">Settings and Users stay with the Administrator.</div></div>';
      if (opts.preview) {
        var cards = on.filter(function (m) { return m.k !== 'Dashboard'; }).slice(0, 4);
        var bar = on.slice(0, 3), more = on.slice(3);
        h += '<div class="la-phone"><div class="cap">What this user sees</div><div class="la-scr"><div class="la-bar"><span>Home</span><span>Limited Access</span></div><div class="la-cards">' +
          (on.length ? (cards.length ? cards.map(function (m) { return '<div class="la-card">' + (st[m.k] === 'v' ? '<i>\u{1F441}</i>' : '') + m.card + '<b>' + m.v + '</b></div>'; }).join('') : '<div class="la-empty">Dashboard shows only cards for screens you allow.</div>') : '<div class="la-empty">No screens yet.</div>') +
          '</div>' + (more.length ? '<div class="la-more"><span style="background:none;padding:0">More</span>' + more.map(function (m) { return '<span>' + m.k + '</span>'; }).join('') + '</div>' : '') +
          '<div class="la-nav">' + bar.map(function (m) { return '<div><i>' + m.ic + '</i>' + m.k + '</div>'; }).join('') + '<div><i>\u{1F464}</i>Profile</div></div></div></div>';
      }
      h += '</div>' +
        '<div class="la-inputs">' + Object.keys(st).map(function (k) { return '<input type="hidden" name="modules" value="' + k + ':' + st[k] + '">'; }).join('') + '</div>';
      el.innerHTML = h;
      el.querySelectorAll('.la-chip').forEach(function (b) { b.onclick = function () { if (PRESETS[b.dataset.p]) { st = parse(PRESETS[b.dataset.p]); render(); } }; });
      el.querySelectorAll('[data-t]').forEach(function (b) { b.onclick = function () { var k = b.dataset.t; if (st[k]) delete st[k]; else st[k] = 'v'; render(); }; });
      el.querySelectorAll('.la-seg button').forEach(function (b) { b.onclick = function () { st[b.dataset.k] = b.dataset.m; render(); }; });
      var hb = el.querySelector('input[name=hide_trip_amounts]'); if (hb) hb.onchange = function () { ex.hide = hb.checked; };
      var sa = el.querySelector('input[name=salary_everyone]');
      // live hint: does the mobile number typed for this login match an employee's mobile number?
      var matchBox = el.querySelector('.la-match');
      if (matchBox) {
        var form = el.closest('form'), mob = form && form.querySelector('input[name=phone], input[name=username], #uMobile');
        var digits10 = function (v) { var d = String(v || '').replace(/\D/g, ''); return d.length >= 10 ? d.slice(-10) : ''; };
        var showMatch = function () {
          var m = mob ? digits10(mob.value) : '', emp = null;
          if (m) (window.LA_EMPLOYEES || []).forEach(function (e) { if (e.mobile === m) emp = e; });
          if (emp) { matchBox.className = 'la-match ok'; matchBox.innerHTML = '\u2714 Own salary: this login\u2019s mobile number matches <b>' + esc(emp.name) + '</b>' + (emp.type ? ' (' + esc(emp.type) + ')' : '') + '.'; }
          else { matchBox.className = 'la-match warn'; matchBox.innerHTML = m ? 'No employee has the mobile number <b>' + esc(m) + '</b> yet \u2014 until one does (Salaries \u2192 employee \u2192 Edit \u2192 Mobile) this person will not see their own salary.'
                                                                  : 'Enter this login\u2019s mobile number above \u2014 it is matched to an employee\u2019s mobile number to find their own salary.'; }
        };
        if (mob) { mob.addEventListener('input', showMatch); mob.addEventListener('change', showMatch); }
        showMatch();
      }
      var ms = el.querySelector('.la-ms');
      if (ms) {
        var field = ms.querySelector('.la-ms-field'), panel = ms.querySelector('.la-ms-panel'), chips = ms.querySelector('.la-ms-chips');
        var search = ms.querySelector('.la-ms-search'), count = ms.querySelector('.la-ms-count'), none = ms.querySelector('.la-ms-empty');
        var picks = ms.querySelectorAll('input[name=salary_ids]'), labels = ms.querySelectorAll('.la-ms-list label');
        var nameOf = function (c) { return c.parentNode.querySelector('span').firstChild.textContent.trim(); };
        var sync = function () {                              // read the boxes back into the state and redraw the chips
          ex.salary_all = !!(sa && sa.checked);
          ex.salary_ids = Array.prototype.filter.call(picks, function (x) { return x.checked && !ex.salary_all; }).map(function (x) { return x.value; });
          var chosen = Array.prototype.filter.call(picks, function (x) { return x.checked && !x.disabled; });
          if (ex.salary_all) chips.innerHTML = '<span class="la-chip2 all">Everyone</span>';
          else if (!chosen.length) chips.innerHTML = '<span class="la-ms-ph">Nobody else \u2014 only their own salary</span>';
          else chips.innerHTML = chosen.slice(0, 5).map(function (c) { return '<span class="la-chip2">' + esc(nameOf(c)) + '<button type="button" data-id="' + c.value + '" aria-label="Remove ' + esc(nameOf(c)) + '">\u00D7</button></span>'; }).join('') +
            (chosen.length > 5 ? '<span class="la-chip2 more">+' + (chosen.length - 5) + ' more</span>' : '');
          count.textContent = ex.salary_all ? 'Everyone' : chosen.length + ' of ' + picks.length + ' selected';
        };
        var setOpen = function (open) { panel.hidden = !open; field.setAttribute('aria-expanded', open ? 'true' : 'false'); ms.classList.toggle('open', open); if (open) search.focus({ preventScroll: true }); };
        field.onclick = function (e) { if (e.target.closest('.la-chip2 button')) return; setOpen(panel.hidden); };
        field.onkeydown = function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(panel.hidden); } else if (e.key === 'Escape' && !panel.hidden) { e.preventDefault(); setOpen(false); } };
        panel.onkeydown = function (e) { if (e.key === 'Escape') { e.preventDefault(); setOpen(false); field.focus({ preventScroll: true }); } };   // closes only the dropdown, not the pop-up around it
        chips.onclick = function (e) {                          // the little × on a chip unticks that person
          var b = e.target.closest('button[data-id]'); if (!b) return;
          Array.prototype.forEach.call(picks, function (c) { if (c.value === b.getAttribute('data-id')) c.checked = false; });
          sync();
        };
        search.oninput = function () {
          var q = search.value.trim().toLowerCase(), shown = 0;
          labels.forEach(function (l) { var ok = !q || l.getAttribute('data-n').indexOf(q) >= 0; l.hidden = !ok; if (ok) shown++; });
          none.hidden = !!shown || !picks.length;
        };
        ms.querySelector('.la-ms-bar').onclick = function (e) {
          var b = e.target.closest('button[data-a]'); if (!b || (sa && sa.checked)) return;
          Array.prototype.forEach.call(picks, function (c) { var l = c.parentNode; if (b.getAttribute('data-a') === 'none') c.checked = false; else if (!l.hidden) c.checked = true; });
          sync();
        };
        if (sa) sa.onchange = function () {                     // "Everyone" ticks and locks the whole list; unticking hands it back
          var keep = ex.salary_ids;
          picks.forEach(function (c) { c.disabled = sa.checked; c.checked = sa.checked || keep.indexOf(c.value) >= 0; });
          sync();
        };
        picks.forEach(function (c) { c.onchange = sync; });
        sync();
      }
    }
    el.laSet = function (txt, extras) { st = parse(txt); takeExtras(extras); render(); };
    el.laValue = function () { return sorted(st).join(','); };
    render();
  }
  window.LAPicker = { mount: mount };
})();
