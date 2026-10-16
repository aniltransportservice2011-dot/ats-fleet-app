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

  // The entry rows on the form (payment received / payout to owner / fuel / driver advance): new ones, stored ones that were edited, and stored ones that were
  // removed. A stored payment that is edited or removed is first undone (its share of the trip's total comes back off) and an edited one is then applied again
  // like a new row — exactly what the server does when the trip is saved.
  function entryInfo(form) {
    var info = { received: [], owner: [], removedReceived: 0, removedOwner: 0, fuel: [], driver_adv: [] };
    if (!form.querySelectorAll) return info;
    form.querySelectorAll('[data-entry-row]').forEach(function (r) {
      var k = r.querySelector('[data-f="kind"]'), a = r.querySelector('[data-f="amount"]'), v = r.querySelector('[data-f="vendor"]');
      var kind = k ? k.value : 'received', amt = num(a ? a.value : 0);
      var stored = r.hasAttribute('data-id') ? r.getAttribute('data-id') : '';
      if (kind === 'received' || kind === 'owner') {
        if (stored && r.getAttribute('data-changed') !== '1') return;                      // an untouched stored payment: already in the totals
        if (stored) { var oa = num(r.getAttribute('data-orig-alloc')); if (kind === 'received') info.removedReceived += oa; else info.removedOwner += oa; }
        if (amt > 0) info[kind].push(amt);
      } else if (amt > 0) {
        info[kind].push({ amount: amt, vendor: v ? v.value : '' });
      }
    });
    form.querySelectorAll('input[name="entry_delete"]').forEach(function (h) {
      var oa = num(h.getAttribute('data-orig-alloc')), k = h.getAttribute('data-kind');
      if (k === 'received') info.removedReceived += oa; else if (k === 'owner') info.removedOwner += oa;
    });
    return info;
  }

  // Rows are saved in order, each capped at what is still owed (the server does exactly this). A row that comes when NOTHING is left is refused
  // by the server — flagged here as `blocked`; a row bigger than what is left is applied up to that, the rest stays in the ledger as an advance.
  function allocate(amounts, owedNow) {
    var left = Math.max(owedNow, 0), applied = 0, entered = 0, blocked = 0;
    amounts.forEach(function (a, i) {
      entered += a;
      if (left <= EPS) { if (!blocked) blocked = i + 1; return; }
      var ap = Math.min(a, left); applied += ap; left -= ap;
    });
    return { entered: round2(entered), applied: round2(applied), over: round2(entered - applied), blocked: blocked };
  }

  // ---- party side ------------------------------------------------------------------------------------------------------------------------
  function party(form, billed) {
    var root = form.querySelector('[data-tpl-root="party"]');
    var dated = root ? num(root.getAttribute('data-dated')) : 0;
    var adv = val(form, 'party_advance');
    var typed = val(form, 'payment_received');
    var eff = Math.max(typed, dated);                       // the server's floor
    // new rows are applied to THIS trip only and never beyond what it still owes (the rest stays in the ledger as an advance) — as the server does
    var info = entryInfo(form);
    var remR = info.removedReceived;
    typed = Math.max(typed - remR, 0); dated = Math.max(dated - remR, 0);
    eff = Math.max(typed, dated);
    var al = allocate(info.received, billed - eff - adv);
    var entered = al.entered, applied = al.applied;
    var received = round2(eff + adv + applied);
    var balance = round2(billed - eff - adv - applied);
    return { root: root, dated: dated, advance: adv, typed: typed, effective: eff, undated: round2(Math.max(eff - dated, 0)),
             entered: entered, applied: applied, over: al.over, blocked: al.blocked,
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
    bar(root, billed, f.advance + f.undated, f.dated + f.applied);
    var ar = part(root, 'adv-row');
    show(ar, f.advance > EPS); text(root, 'adv-amt', money(f.advance));
    var lr = part(root, 'lump-row');
    show(lr, f.undated > EPS); text(root, 'lump-amt', money(f.undated));
    var note = part(root, 'note');
    if (note) {
      var msg = '';
      if (root.hasAttribute('data-add') && f.advance > billed + 0.011) msg = 'Party advance can\u2019t be more than the trip bill.';    // the server refuses this on a new trip
      else if (f.blocked) msg = 'Entry ' + f.blocked + ': nothing is left to receive on this trip. Remove it before saving.';
      else if (f.over > EPS) msg = money(f.over) + ' of what you entered is more than this trip still owes. It stays in the ledger as an advance.';
      else if (f.floored) msg = '₹' + Math.round(f.dated).toLocaleString('en-US') + ' is already recorded in the ledger for this trip, so Payment Received can’t go lower than that.';
      note.textContent = msg;
      note.hidden = !msg;
    }
    return f;
  }

  // ---- owner side (hired trips) ------------------------------------------------------------------------------------------------------------
  function norm(el) { return ((el || {}).value || '').trim().toLowerCase(); }
  // Fuel / driver advance the COMPANY paid on this trip: every part whose vendor is not the owner himself (a blank vendor counts — it was paid directly).
  // The parts are the "earlier total" (legacy_* fields) plus each dated entry with its own vendor.
  function companyPaid(form) {
    var owner = norm(field(form, 'owner_name'));
    if (!owner) return 0;                                    // no owner chosen: nothing is "paid on his behalf"
    var sum = 0;
    if (field(form, 'legacy_fuel_amount')) {
      sum += (norm(field(form, 'legacy_fuel_vendor')) !== owner ? val(form, 'legacy_fuel_amount') : 0);
      sum += (norm(field(form, 'legacy_adv_vendor')) !== owner ? val(form, 'legacy_adv_amount') : 0);
    } else {
      sum += (norm(field(form, 'fuel_vendor')) !== owner ? val(form, 'fuel_amount') : 0);
      sum += (norm(field(form, 'driver_adv_vendor')) !== owner ? val(form, 'driver_adv_amount') : 0);
    }
    var info = entryInfo(form);
    ['fuel', 'driver_adv'].forEach(function (k) {
      info[k].forEach(function (e) { if ((e.vendor || '').trim().toLowerCase() !== owner) sum += e.amount; });
    });
    return sum;
  }

  function updateOwner(form, ownerAmount, isHired) {
    var root = form.querySelector('[data-tpl-root="owner"]');
    if (!root) return;
    root.hidden = !isHired;
    if (!isHired) return;
    var dated = num(root.getAttribute('data-dated'));
    var typed = val(form, 'paid_to_owner');
    var info = entryInfo(form);
    typed = Math.max(typed - info.removedOwner, 0); dated = Math.max(dated - info.removedOwner, 0);
    var eff = Math.max(typed, dated);
    var company = round2(companyPaid(form));
    var al = allocate(info.owner, ownerAmount - eff - company);
    var entered = al.entered, applied = al.applied;
    var pending = round2(ownerAmount - eff - company - applied);
    text(root, 'owner-amount', money(ownerAmount));
    text(root, 'company', money(company));
    text(root, 'paid', money(eff + applied));
    text(root, 'owner-net', money(ownerAmount - company));
    text(root, 'pending', money(pending > 0 ? pending : 0));
    tone(root, 'pending', pending > EPS);
    pill(root, status(ownerAmount, pending, (eff + applied + company) > EPS));
    var undated = round2(Math.max(eff - dated, 0));
    bar(root, ownerAmount, undated, dated + applied);
    show(part(root, 'lump-row'), undated > EPS); text(root, 'lump-amt', money(undated));
    var note = part(root, 'note');
    if (note) {
      var msg = '';
      if (al.blocked) msg = 'Entry ' + al.blocked + ': nothing is left to pay the owner on this trip. Remove it before saving.';
      else if (round2(entered - applied) > EPS) msg = money(round2(entered - applied)) + ' of what you entered is more than the owner is still owed. It stays in the ledger as an advance.';
      else if (typed + EPS < dated) msg = '₹' + Math.round(dated).toLocaleString('en-US') + ' is already recorded in the ledger for this trip, so the amount paid can’t go lower than that.';
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
