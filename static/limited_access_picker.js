/* LAPicker — choose which screens a Limited Access user may open, and View or Edit for each.
   Writes hidden inputs  name="modules"  value="Trips:e" / "Ledger:v"  (what add_user / edit_user expect). */
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
      h += '</div><div class="la-inputs">' + Object.keys(st).map(function (k) { return '<input type="hidden" name="modules" value="' + k + ':' + st[k] + '">'; }).join('') + '</div>';
      el.innerHTML = h;
      el.querySelectorAll('.la-chip').forEach(function (b) { b.onclick = function () { if (PRESETS[b.dataset.p]) { st = parse(PRESETS[b.dataset.p]); render(); } }; });
      el.querySelectorAll('[data-t]').forEach(function (b) { b.onclick = function () { var k = b.dataset.t; if (st[k]) delete st[k]; else st[k] = 'v'; render(); }; });
      el.querySelectorAll('.la-seg button').forEach(function (b) { b.onclick = function () { st[b.dataset.k] = b.dataset.m; render(); }; });
    }
    el.laSet = function (txt) { st = parse(txt); render(); };
    el.laValue = function () { return sorted(st).join(','); };
    render();
  }
  window.LAPicker = { mount: mount };
})();
