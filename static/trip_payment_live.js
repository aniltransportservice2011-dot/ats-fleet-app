/* Live payment figures on the trip forms (website Add / Edit Trip, phone Add / Update Trip wizard).

   While the person types — Party Advance, Payment Received, Paid to Owner, or anything that changes the billed / owner amount — the Received, Balance,
   Pending, status pill, progress bar and the undated rows in the Payment Tracking card are recomputed on the spot, from what is in the form right now.
   Nothing is saved until the trip is saved: leave the page and everything reverts to what is stored; save, and the stored numbers are exactly what the
   card showed.

   The arithmetic is the server's own (see _trip_payment_block() / _update_trip_from_form() in app.py):
     party   balance = billed - max(payment_received, ledger payments already applied) - party_advance
     owner   pending = owner amount - max(paid_to_owner, ledger payments already applied) - fuel/driver advance the COMPANY paid
             (a fuel / driver advance counts as company-paid when its vendor is not the owner)
   "max(…, ledger payments)" is the same floor the server applies on save: a typed total can never drop below what the Ledger has already applied.

   Markup contract (both partials): a root  [data-tpl-root="party"|"owner"]  carrying data-dated (ledger payments applied to this trip) and the
   values stored today (data-init-*); inside it, elements marked  data-tpl="billed|received|balance|…" . The script only fills in what is present.
   Public: TripPayLive.update(form, billed, ownerAmount, isHired)  and  TripPayLive.party(form, billed)  (the phone review step reuses it). */
(function () {
  'use strict';
  var EPS = 0.004;
  var CLS = { 'Paid': 'paid', 'Partly paid': 'part', 'Pending': 'pend' };

  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : 0; }
  function field(form, name) { return form && form.elements ? form.elements[name] : null; }
  function val(form, name) { var el = field(form, name); return el ? num(el.value) : 0; }
  function round2(v) { return Math.round(v * 100) / 100; }
  function money(v) {
    var r = round2(v), neg = r < 0, a = Math.abs(r);
    var s = Math.abs(a - Math.round(a)) < 1e-9 ? Math.round(a).toLocaleString('en-US') : a.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (neg ? '-' : '') + '₹' + s;
  }
  function status(total, due, paidAny) {
    if (total <= EPS) return '';
    if (due <= EPS) return 'Paid';
    return paidAny ? 'Partly paid' : 'Pending';
  }
  function part(root, name) { return root.querySelector('[data-tpl="' + name + '"]'); }
  function text(root, name, v) { var el = part(root, name); if (el) el.textContent = v; return el; }
  function tone(root, name, bad) {
    var el = part(root, name);
    if (el) { el.classList.toggle('tpl-bad', bad); el.classList.toggle('tpl-good', !bad); }
  }
  function show(el, on) { if (el) el.hidden = !on; }
  function pill(root, st) {
    var el = part(root, 'pill');
    if (!el) return;
    el.hidden = !st;
    if (!st) return;
    var p = el.getAttribute('data-prefix') || 'tph';
    el.textContent = st;
    el.className = p + '-pill ' + p + '-' + CLS[st];
  }
  function bar(root, total, undated, dated) {
    var b = part(root, 'bar');
    if (!b) return;
    b.hidden = !(total > EPS);
    if (!(total > EPS)) return;
    var u = Math.min(undated / total * 100, 100);
    var d = Math.min(dated / total * 100, 100 - u);
    var iu = part(root, 'bar-u'), id = part(root, 'bar-d');
    if (iu) iu.style.width = Math.max(u, 0) + '%';
    if (id) id.style.width = Math.max(d, 0) + '%';
  }

  // ---- party side ------------------------------------------------------------------------------------------------------------------------
  function party(form, billed) {
    var root = form.querySelector('[data-tpl-root="party"]');
    var dated = root ? num(root.getAttribute('data-dated')) : 0;
    var adv = val(form, 'party_advance');
    var typed = val(form, 'payment_received');
    var eff = Math.max(typed, dated);                       // the server's floor
    var received = round2(eff + adv);
    var balance = round2(billed - eff - adv);
    return { root: root, dated: dated, advance: adv, typed: typed, effective: eff, undated: round2(Math.max(eff - dated, 0)),
             received: received, balance: balance, floored: typed + EPS < dated, status: status(billed, balance, received > EPS) };
  }

  function updateParty(form, billed) {
    var f = party(form, billed), root = f.root;
    if (!root) return f;
    text(root, 'billed', money(billed));
    text(root, 'received', money(f.received));
    text(root, 'balance', money(f.balance > 0 ? f.balance : 0));
    tone(root, 'balance', f.balance > EPS);
    pill(root, f.status);
    bar(root, billed, f.advance + f.undated, f.dated);
    var none = part(root, 'none');
    if (none) none.hidden = !(num(root.getAttribute('data-nrows')) === 0 && f.advance <= EPS && f.undated <= EPS);
    var ar = part(root, 'adv-row');
    show(ar, f.advance > EPS); text(root, 'adv-amt', money(f.advance));
    var lr = part(root, 'lump-row');
    show(lr, f.undated > EPS); text(root, 'lump-amt', money(f.undated));
    // on a brand-new trip there is nothing stored to go back to, so no "not saved yet" line there
    var changed = !root.hasAttribute('data-add') && (Math.abs(f.advance - num(root.getAttribute('data-init-adv'))) > EPS ||
                  Math.abs(f.typed - num(root.getAttribute('data-init-received'))) > EPS ||
                  Math.abs(billed - num(root.getAttribute('data-init-billed'))) > EPS);
    var note = part(root, 'note');
    if (note) {
      var msg = '';
      if (root.hasAttribute('data-add') && f.advance > billed + 0.011) msg = 'Party advance can\u2019t be more than the trip bill.';    // the server refuses this on a new trip
      else if (f.floored) msg = '₹' + Math.round(f.dated).toLocaleString('en-US') + ' is already recorded in the ledger for this trip, so Payment Received can’t go lower than that.';
      else if (changed) msg = 'Not saved yet — these figures change only when you save the trip. Leave the page and they go back to what was saved.';
      note.textContent = msg;
      note.hidden = !msg;
    }
    return f;
  }

  // ---- owner side (hired trips) ------------------------------------------------------------------------------------------------------------
  function companyPaid(form) {
    var owner = ((field(form, 'owner_name') || {}).value || '').trim().toLowerCase();
    if (!owner) return 0;                                    // no owner chosen: nothing is "paid on his behalf"
    var fv = ((field(form, 'fuel_vendor') || {}).value || '').trim().toLowerCase();
    var dv = ((field(form, 'driver_adv_vendor') || {}).value || '').trim().toLowerCase();
    return (fv !== owner ? val(form, 'fuel_amount') : 0) + (dv !== owner ? val(form, 'driver_adv_amount') : 0);
  }

  function updateOwner(form, ownerAmount, isHired) {
    var root = form.querySelector('[data-tpl-root="owner"]');
    if (!root) return;
    root.hidden = !isHired;
    if (!isHired) return;
    var dated = num(root.getAttribute('data-dated'));
    var typed = val(form, 'paid_to_owner');
    var eff = Math.max(typed, dated);
    var company = round2(companyPaid(form));
    var pending = round2(ownerAmount - eff - company);
    text(root, 'owner-amount', money(ownerAmount));
    text(root, 'company', money(company));
    text(root, 'paid', money(eff));
    text(root, 'owner-net', money(ownerAmount - company));
    text(root, 'pending', money(pending > 0 ? pending : 0));
    tone(root, 'pending', pending > EPS);
    pill(root, status(ownerAmount, pending, (eff + company) > EPS));
    var undated = round2(Math.max(eff - dated, 0));
    bar(root, ownerAmount, undated, dated);
    show(part(root, 'lump-row'), undated > EPS); text(root, 'lump-amt', money(undated));
    var none = part(root, 'none');
    if (none) none.hidden = !(num(root.getAttribute('data-nrows')) === 0 && undated <= EPS);
    var note = part(root, 'note');
    if (note) {
      var msg = '';
      if (typed + EPS < dated) msg = '₹' + Math.round(dated).toLocaleString('en-US') + ' is already recorded in the ledger for this trip, so the amount paid can’t go lower than that.';
      else if (!root.hasAttribute('data-add') && (Math.abs(typed - num(root.getAttribute('data-init-paid'))) > EPS || Math.abs(ownerAmount - num(root.getAttribute('data-init-owner'))) > EPS))
        msg = 'Not saved yet — these figures change only when you save the trip.';
      note.textContent = msg;
      note.hidden = !msg;
    }
  }

  window.TripPayLive = {
    update: function (form, billed, ownerAmount, isHired) {
      if (!form) return;
      updateParty(form, billed);
      updateOwner(form, ownerAmount, !!isHired);
    },
    party: party,
    money: money
  };
})();
