/* MyGuest booking widget.
 *
 * A hotel pastes one tag into its own website:
 *
 *   <script src="https://<your MyGuest address>/widget.js"
 *           data-property="559167" async></script>
 *
 * and gets a small "check in / check out / guests / Check availability" box
 * where the tag sits. Pressing the button opens that hotel's MyGuest booking
 * page with the search already filled in, so the guest sees rooms and prices
 * straight away.
 *
 * Deliberately small and dependency-free, and drawn inside a Shadow DOM so
 * the hotel's CSS cannot break it and it cannot break theirs.
 *
 * It sends nothing and stores nothing. It only builds a link. The booking
 * page itself does the availability check, the hold and the payment.
 *
 * Optional attributes:
 *   data-color   the button colour, as #rrggbb   (default MyGuest teal)
 *   data-target  the id of an element to draw into (default: right after the tag)
 *   data-lang    the booking page's language      (default: the guest's own)
 */
(function () {
  var script = document.currentScript;
  if (!script) return;
  var code = (script.getAttribute('data-property') || '').trim();
  if (!/^[A-Za-z0-9-]{3,40}$/.test(code)) {
    if (window.console) console.warn('MyGuest widget: data-property is missing or invalid.');
    return;
  }
  var origin = new URL(script.src, location.href).origin;
  var color = script.getAttribute('data-color') || '';
  if (!/^#[0-9a-fA-F]{6}$/.test(color)) color = '#007A85';
  var lang = (script.getAttribute('data-lang') || '').trim();

  var host = document.createElement('div');
  var target = script.getAttribute('data-target');
  var slot = target && document.getElementById(target);
  if (slot) slot.appendChild(host); else script.parentNode.insertBefore(host, script.nextSibling);

  var root = host.attachShadow ? host.attachShadow({ mode: 'open' }) : host;
  var iso = function (d) { return d.toISOString().slice(0, 10); };
  var today = new Date();
  var plus = function (n) { var d = new Date(today); d.setDate(d.getDate() + n); return d; };

  root.innerHTML =
    '<style>' +
    ':host{all:initial}' +
    '.mg{font:14px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1e293b;' +
    'display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;background:#fff;' +
    'border:1px solid #e2e8f0;border-radius:14px;padding:14px;box-shadow:0 4px 18px rgba(16,35,68,.08)}' +
    '.f{display:flex;flex-direction:column;gap:4px;flex:1 1 130px;min-width:120px}' +
    'label{font-size:12px;font-weight:600;color:#475569}' +
    'input,select{font:inherit;padding:9px 10px;border:1px solid #cbd5e1;border-radius:9px;background:#fff;color:#1e293b}' +
    'button{font:inherit;font-weight:600;border:0;border-radius:10px;padding:11px 18px;color:#fff;' +
    'background:' + color + ';cursor:pointer;flex:1 1 160px}' +
    'button:hover{filter:brightness(.92)}' +
    '.by{flex-basis:100%;font-size:11px;color:#94a3b8;text-align:right}' +
    '</style>' +
    '<form class="mg" part="widget">' +
    '<div class="f"><label for="a">Check in</label><input id="a" type="date" required></div>' +
    '<div class="f"><label for="d">Check out</label><input id="d" type="date" required></div>' +
    '<div class="f" style="flex-basis:90px"><label for="g">Guests</label><select id="g"></select></div>' +
    '<button type="submit">Check availability</button>' +
    '<div class="by">Powered by MyGuest</div>' +
    '</form>';

  var $ = function (id) { return root.querySelector('#' + id); };
  $('a').value = iso(plus(1)); $('a').min = iso(today);
  $('d').value = iso(plus(3)); $('d').min = iso(plus(1));
  for (var i = 1; i <= 10; i++) {
    var o = document.createElement('option'); o.value = String(i); o.textContent = String(i);
    $('g').appendChild(o);
  }
  $('g').value = '2';
  $('a').addEventListener('change', function () {
    if ($('d').value <= $('a').value) {
      var d = new Date($('a').value); d.setDate(d.getDate() + 1); $('d').value = iso(d);
    }
    $('d').min = $('a').value;
  });

  root.querySelector('form').addEventListener('submit', function (e) {
    e.preventDefault();
    if (!$('a').value || !$('d').value || $('d').value <= $('a').value) {
      $('d').focus();
      return;
    }
    var q = new URLSearchParams({ arrival: $('a').value, departure: $('d').value,
                                  adults: $('g').value });
    if (lang) q.set('lang', lang);
    window.open(origin + '/book/' + encodeURIComponent(code) + '?' + q.toString(),
                '_blank', 'noopener');
  });
})();
