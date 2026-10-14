/* Suggests the next LR number on the Add Trip form (format: Settings > Invoice > Trip Numbering).
   - fills the LR field when it is empty or still holds an earlier suggestion; never overwrites something the person typed
   - follows the Trip Date and the "Empty Trip" switch (an empty trip gets the empty-trip format)
   - a small "Use suggested" link brings the suggestion back after editing
   Usage: TripNumberSuggest.init({ url, dateEl, lrEl, emptyEl }) */
(function () {
  function today() {
    var d = new Date(), m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + '-' + (m < 10 ? '0' : '') + m + '-' + (day < 10 ? '0' : '') + day;
  }
  window.TripNumberSuggest = {
    init: function (o) {
      var lr = o.lrEl, dateEl = o.dateEl, emptyEl = o.emptyEl;
      if (!lr || !o.url) return;
      var auto = '', typed = false, latest = '', seq = 0;
      var hint = document.createElement('div');
      hint.style.cssText = 'font-size:0.72rem;margin-top:4px;opacity:0.75;display:none;';
      var link = document.createElement('a');
      link.href = '#'; link.textContent = 'Use suggested'; link.style.cssText = 'margin-left:6px;font-weight:600;text-decoration:underline;';
      lr.parentNode.insertBefore(hint, lr.nextSibling);

      function query() {
        var q = '?date=' + encodeURIComponent((dateEl && dateEl.value) || today()) + '&empty=' + (emptyEl && emptyEl.checked ? '1' : '0');
        var mine = ++seq;
        fetch(o.url + q, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) {
            if (!j || !j.ok || mine !== seq) return;
            latest = j.number;
            if (!typed || lr.value.trim() === '' || lr.value === auto) { lr.value = latest; auto = latest; typed = false; }
            render();
          })
          .catch(function () {});
      }
      function render() {
        if (!latest) { hint.style.display = 'none'; return; }
        hint.textContent = lr.value === latest ? 'Suggested from your numbering format — you can edit it.' : 'Suggested: ' + latest;
        if (lr.value !== latest) hint.appendChild(link);
        hint.style.display = '';
      }
      link.addEventListener('click', function (e) { e.preventDefault(); lr.value = latest; auto = latest; typed = false; render(); lr.dispatchEvent(new Event('input', { bubbles: true })); });
      lr.addEventListener('input', function (e) { if (e.isTrusted !== false) { typed = lr.value.trim() !== '' && lr.value !== auto; render(); } });
      if (dateEl) dateEl.addEventListener('change', query);
      if (emptyEl) emptyEl.addEventListener('change', query);
      if (lr.value.trim() === '') query();
      else { latest = ''; query(); typed = true; }                    // something is already there (a failed save re-shown): keep it, still offer the suggestion
    }
  };
})();
