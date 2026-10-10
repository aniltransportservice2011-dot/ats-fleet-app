/* Keeps mobile-number inputs to a 10-digit Indian number while typing/pasting.
   The server (USERNAME_MOBILE_RE in app.py) is the real check — this only stops the
   field from ever holding more than 10 digits, and cleans up a pasted "+91 98765 43210".

   data-mobile-input="strict" — a mobile number only: non-digits are dropped.
   data-mobile-input="mixed"  — mobile number OR email (login/sign-up username): the field is
                                only touched while it holds nothing but digits/spaces/+/-;
                                the moment a letter or @ appears it's treated as an email and
                                left alone. */
(function () {
  function clean(el) {
    var v = el.value;
    if (el.getAttribute('data-mobile-input') === 'mixed' && !/^[\d\s+\-]*$/.test(v)) return;
    var d = v.replace(/\D/g, '');
    if (d.length === 12 && d.indexOf('91') === 0) d = d.slice(2);   // +91 / 91 prefix
    else if (d.length === 11 && d.charAt(0) === '0') d = d.slice(1); // leading 0
    d = d.slice(0, 10);
    if (d !== v) el.value = d;
  }
  document.querySelectorAll('[data-mobile-input]').forEach(function (el) {
    el.addEventListener('input', function () { clean(el); });
    clean(el);
  });
})();
