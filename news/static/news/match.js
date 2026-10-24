/*
 * Ukurasa wa mechi / habari ya LIVE unaojisasisha wenyewe (news/views.article, news/live.py).
 *
 * Kila sekunde 20 unauliza /habari/live/<pk>.json:
 *   - masasisho mapya ya matukio yanaingizwa juu ya orodha (bila kugusa mengine)
 *   - takwimu (umiliki, xG, mashuti...) zinabadilika papo hapo
 *   - alama `v` ikibadilika (mechi imeanza/imeisha, vikosi, video, uchambuzi, kichwa) sehemu za
 *     ukurasa zenye [data-zone] zinachukuliwa upya kutoka seva na kubadilishwa — bila kurefresh.
 */
(function () {
  var body = document.getElementById('mz-body');
  if (!body) return;
  var tab = null, busy = false;

  function tabs(root) {
    var box = root.querySelector('#mp');
    if (!box) return;
    var buttons = box.querySelectorAll('[data-tab]'), panes = box.querySelectorAll('[data-pane]');
    function show(name) {
      tab = name;
      buttons.forEach(function (b) { b.setAttribute('aria-selected', b.dataset.tab === name ? 'true' : 'false'); });
      panes.forEach(function (p) { p.hidden = p.dataset.pane !== name; });
    }
    buttons.forEach(function (b) { b.addEventListener('click', function () { show(b.dataset.tab); }); });
    var keep = tab && box.querySelector('[data-tab="' + tab + '"]') ? tab : null;
    var first = box.dataset.first && box.querySelector('[data-tab="' + box.dataset.first + '"]') ? box.dataset.first : null;
    if (buttons.length) show(keep || first || buttons[0].dataset.tab);
  }

  function lastId() {
    var f = body.querySelector('#lvList .lv-item');
    return f ? f.dataset.id : 0;
  }

  function addUpdates(list) {
    var ol = body.querySelector('#lvList');
    if (!ol || !list.length) return;
    var empty = body.querySelector('#lvEmpty');
    list.slice().reverse().forEach(function (u) {
      if (document.getElementById('u' + u.id)) return;
      if (empty) { empty.remove(); empty = null; }
      var li = document.createElement('li');
      li.className = 'lv-item new' + (u.kind ? ' k-' + u.kind : '');
      li.id = 'u' + u.id; li.dataset.id = u.id;
      var when = document.createElement('div'); when.className = 'lv-when'; when.textContent = u.label || '';
      var sm = document.createElement('small'); sm.textContent = u.time; when.appendChild(sm);
      var txt = document.createElement('div'); txt.className = 'lv-text'; txt.innerHTML = u.html;   // imesafishwa kwenye seva
      li.appendChild(when); li.appendChild(txt);
      ol.insertBefore(li, ol.firstChild);
    });
  }

  function setStats(stats) {
    (stats || []).forEach(function (s) {
      var row = body.querySelector('[data-stat="' + s.name.replace(/"/g, '') + '"]');
      if (!row) return;
      row.querySelector('[data-h]').textContent = s.home;
      row.querySelector('[data-a]').textContent = s.away;
      row.querySelector('.mp-bar i').style.width = s.pct + '%';
    });
  }

  function swap() {
    if (busy) return;
    busy = true;
    fetch(location.pathname + '?_=' + Date.now(), {cache: 'no-store', credentials: 'same-origin'})
      .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, 'text/html');
        document.querySelectorAll('[data-zone]').forEach(function (el) {
          var fresh = doc.getElementById(el.id);
          if (!fresh) return;
          el.innerHTML = fresh.innerHTML;
          Array.prototype.forEach.call(fresh.attributes, function (a) { el.setAttribute(a.name, a.value); });
        });
        if (doc.title) document.title = doc.title;
        body.classList.remove('mz-flash'); void body.offsetWidth; body.classList.add('mz-flash');
        tabs(body);
      })
      .catch(function () {})
      .then(function () { busy = false; });
  }

  function tick() {
    if (document.hidden || body.dataset.active !== '1') return;
    fetch(body.dataset.feed + '?after=' + lastId(), {cache: 'no-store'})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        addUpdates(d.updates || []);
        if (d.match) setStats(d.match.stats);
        if (d.v && d.v !== body.dataset.v) swap();
      })
      .catch(function () {});
  }

  tabs(body);
  setInterval(tick, 20000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
})();
