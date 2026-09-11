(function () {
  "use strict";
  // Net worth: dated balance snapshots per account (manual/net_worth.json)
  // combined with series derived from data already on the dashboard - the
  // UOB ONE closing balance per statement month and, when the Home register
  // records a loan balance, the mortgage. Nothing here is a transaction.
  var root = typeof document !== 'undefined' ? document.getElementById('pane-networth') : null;
  if (!root && typeof module === 'undefined') return;
  var GROUPS = [
    ['cash', 'Cash', 'Bank balances from statements and any other cash accounts.'],
    ['cpf', 'CPF', 'Ordinary, Special and MediSave balances read from the CPF portal.'],
    ['investments', 'Investments', 'Brokerage, SRS and fixed deposits at their portal value.'],
    ['insurance', 'Insurance', 'Policies at net surrender or maturity value, not premiums paid.'],
    ['property', 'Property', 'Home at an estimated value; leave blank to keep it out of the total.'],
    ['liabilities', 'Liabilities', 'Loans and other amounts owed, subtracted from the total.']
  ];
  var GROUP_NAME = {}; GROUPS.forEach(function (g) { GROUP_NAME[g[0]] = g[1]; });
  var store = { revision: 0, accounts: [], snapshots: [] }, editable = false, busy = false, derived = [], flows = [], openHistory = {};

  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function money(v, dp) { return v == null ? '—' : (v < 0 ? '-' : '') + 'S$' + Math.abs(v).toLocaleString('en-SG', { minimumFractionDigits: dp == null ? 2 : dp, maximumFractionDigits: dp == null ? 2 : dp }); }
  function money0(v) { return money(v == null ? null : Math.round(v), 0); }
  function signed(v) { return (v > 0 ? '+' : v < 0 ? '−' : '') + 'S$' + Math.abs(Math.round(v)).toLocaleString('en-SG'); }
  // Fixed month names: Chrome's en-GB and en-SG both render September as
  // "Sept", and the rest of the dashboard says "Sep".
  var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function date(v) { return v ? parseInt(v.slice(8, 10), 10) + ' ' + MONTHS[parseInt(v.slice(5, 7), 10) - 1] + ' ' + v.slice(0, 4) : '—'; }
  function today() { var d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function monthOf(d) { return d.slice(0, 7); }
  function monthEnd(m) { var y = +m.slice(0, 4), mo = +m.slice(5, 7); return m + '-' + String(new Date(Date.UTC(y, mo, 0)).getUTCDate()).padStart(2, '0'); }
  function addMonths(m, n) { var y = +m.slice(0, 4), mo = +m.slice(5, 7) - 1 + n; return (y + Math.floor(mo / 12)) + '-' + String((mo % 12 + 12) % 12 + 1).padStart(2, '0'); }
  function monthLabel(m) { return MONTHS[parseInt(m.slice(5, 7), 10) - 1] + ' ' + m.slice(0, 4); }
  function monthsBetween(a, b) { return (+b.slice(0, 4) - +a.slice(0, 4)) * 12 + (+b.slice(5, 7) - +a.slice(5, 7)); }
  function ageDays(d, now) { return Math.floor((Date.parse((now || today()) + 'T00:00:00Z') - Date.parse(d + 'T00:00:00Z')) / 86400000); }
  function snapshotDefaults(accounts, snapshots, id) {
    var account = accounts.find(function (a) { return a.id === id; }) || {};
    var last = snapshots.filter(function (s) { return s.accountId === id; }).sort(function (a, b) { return b.date.localeCompare(a.date); })[0];
    return { source: last && last.source || account.source || '', placeholder: last ? String(last.value) : '0.00' };
  }
  function homeEquity(all, values) {
    return all.reduce(function (sum, s) {
      return sum + (s.account.group === 'property' || s.sourceType === 'home' ? values[s.account.id] || 0 : 0);
    }, 0);
  }
  function replaceHomeSeries(series, home) { return series.filter(function (s) { return s.sourceType !== 'home'; }).concat(deriveMortgage(home)); }
  var ICONS = { home: '<path d="m3 11 9-8 9 8v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>', close: '<path d="M6 6l12 12M18 6 6 18"/>', plus: '<path d="M12 5v14M5 12h14"/>', down: '<path d="m6 10 6 6 6-6"/>', up: '<path d="m6 14 6-6 6 6"/>', trend: '<path d="M3 17l5-5 4 4 9-9"/><path d="M15 7h6v6"/>', scale: '<path d="M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0L7 7Zm10 0-3 7a3 3 0 0 0 6 0l-3-7Z"/>' };
  function icon(name) { return '<svg class="nw-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + '</svg>'; }
  async function json(url) { if (window.FinanceData && url.indexOf('api/') !== 0) return window.FinanceData.load(url); var r = await fetch(url, { cache: 'no-store' }); if (!r.ok) throw new Error('Could not load ' + url); return r.json(); }

  // ---------- Series ----------
  // Every account becomes {account, points:[{date,value,source}]}; a value
  // carries forward from its snapshot date until the next snapshot.
  function allAccounts() {
    return derived.concat(store.accounts.filter(function (a) { return !a.archived; }).map(function (a) {
      var points = store.snapshots.filter(function (s) { return s.accountId === a.id; }).slice().sort(function (x, y) { return x.date.localeCompare(y.date); });
      return { account: a, points: points, manual: true };
    }));
  }
  function valueAt(series, month) {
    var end = monthEnd(month), hit = null;
    series.points.forEach(function (p) { if (p.date <= end) hit = p; });
    return hit;
  }
  function latest(series) { return series.points.length ? series.points[series.points.length - 1] : null; }
  function monthRange(all) {
    var first = null;
    all.forEach(function (s) { if (s.points.length) { var m = monthOf(s.points[0].date); if (!first || m < first) first = m; } });
    if (!first) return [];
    var months = [], m = first, last = monthOf(today());
    while (m <= last) { months.push(m); m = addMonths(m, 1); }
    return months;
  }
  function totalsFor(all, month) {
    var byGroup = {}, assets = 0, liabilities = 0, values = {};
    all.forEach(function (s) {
      var p = valueAt(s, month); if (!p) return;
      var g = s.account.group; byGroup[g] = (byGroup[g] || 0) + p.value;
      values[s.account.id] = g === 'liabilities' ? -p.value : p.value;
      if (g === 'liabilities') liabilities += p.value; else assets += p.value;
    });
    return { month: month, byGroup: byGroup, values: values, assets: assets, liabilities: liabilities, net: assets - liabilities };
  }
  // Change is measured only across accounts recorded at both dates; an account
  // that first appears in between (a loan balance read for the first time, a
  // new broker) would otherwise read as money gained or lost.
  function changeBetween(all, then, now) {
    var delta = 0, excluded = [];
    all.forEach(function (s) {
      var id = s.account.id, a = then.values[id], b = now.values[id];
      if (a != null && b != null) delta += b - a;
      else if (a != null || b != null) excluded.push(s.account.name);
    });
    return { delta: delta, excluded: excluded };
  }

  // ---------- Rendering ----------
  function render() {
    var all = allAccounts(), months = monthRange(all), history = months.map(function (m) { return totalsFor(all, m); });
    var now = history.length ? history[history.length - 1] : { assets: 0, liabilities: 0, net: 0, byGroup: {} };
    var oldest = null, newest = null;
    all.forEach(function (s) { var p = latest(s); if (p) { if (!oldest || p.date < oldest) oldest = p.date; if (!newest || p.date > newest) newest = p.date; } });
    var back = history.length > 12 ? history[history.length - 13] : history[0];
    var change = back && history.length > 1 ? changeBetween(all, back, now) : null;
    var span = back ? monthsBetween(back.month, now.month) : 0;
    var homeOnly = homeEquity(all, now.values || {}), showExcl = all.some(function (s) { return s.account.group === 'property' || s.sourceType === 'home'; });
    var latestNote = newest ? 'Latest balance ' + date(newest) + (oldest && oldest !== newest ? ' \u00b7 oldest ' + date(oldest) : '') : 'No balances recorded yet';
    var assetNote = Object.keys(now.byGroup).filter(function (g) { return g !== 'liabilities' && now.byGroup[g]; }).map(function (g) { return GROUP_NAME[g] + ' ' + money0(now.byGroup[g]); }).join(' \u00b7 ') || 'nothing recorded';
    var changeTile = kpi(span ? 'Change over ' + span + ' month' + (span === 1 ? '' : 's') : 'Change', change == null ? '\u2014' : signed(change.delta), back && span ? 'since ' + monthLabel(back.month) + (change && change.excluded.length ? ' \u00b7 excludes ' + change.excluded.join(', ') + ' (not recorded at both dates)' : '') : 'needs two dated balances', change == null ? null : change.delta >= 0 ? 'good' : 'bad', 'trend');
    // One row of tiles carries every headline figure; the explanations sit
    // in a collapsed note beneath instead of a sentence on every block.
    root.innerHTML =
      '<header class="nw-heading"><div><h2>Net worth</h2><p class="nw-muted">' + latestNote + (staleAccounts(all).length ? ' \u00b7 <span class="nw-stale">over 90 days old: ' + esc(staleAccounts(all).join(', ')) + '</span>' : '') + '</p></div></header>' +
      '<div class="nw-kpis">' +
        kpi(showExcl ? 'Net worth incl. property' : 'Net worth', money0(history.length ? now.net : null), showExcl ? 'personal accounts plus whole-property equity' : 'assets minus liabilities', null, 'scale') +
        (showExcl ? kpi('Personal accounts', money0(now.net - homeOnly), 'excludes property and the home loan', null, 'up') +
                    kpi('Property equity', money0(homeOnly), 'recorded value less the full home loan', null, 'home') : '') +
        kpi('Assets', money0(now.assets), (now.liabilities ? 'less ' + money0(now.liabilities) + ' liabilities \u00b7 ' : '') + assetNote, null, 'up') +
        changeTile +
        (showExcl ? '' : kpi('CPF', money0(now.byGroup.cpf == null ? null : now.byGroup.cpf), cpfNote(all), null, 'scale')) +
      '</div>' +
      '<details class="nw-about"><summary>How these figures are counted</summary><p>Assets minus liabilities, from balances you record here and the bank balances already read from statements. Each account holds its last recorded balance until the next one, so the as-at dates matter.' + (showExcl ? ' Personal accounts exclude property and the home loan but keep other debts; property equity is the recorded value less the full loan, with no ownership share applied, and the recorded value is acquisition cost, not a market valuation.' : '') + ' The 12-month change compares only accounts recorded at both dates.</p></details>' +
      '<section class="nw-panel"><div class="nw-panel-head"><div><h3>History</h3><p class="nw-muted">Month-end view</p></div></div><div class="nw-chart" id="nw-chart"></div></section>' +
      flowsHTML() +
      '<section class="nw-panel"><div class="nw-panel-head"><div><h3>Balances</h3><p class="nw-muted">' + (editable ? 'Record a balance whenever you read one from a portal or statement.' : 'Read-only view.') + '</p></div><div class="nw-actions">' + (editable ? '<button class="nw-button" id="nw-add-account">' + icon('plus') + 'Add account</button><button class="nw-button primary" id="nw-add-snapshot">' + icon('plus') + 'Record balance</button>' : '') + '</div></div><div class="nw-groups">' + GROUPS.map(function (g) { return groupHTML(g, all.filter(function (s) { return s.account.group === g[0]; }), now.byGroup[g[0]]); }).join('') + '</div></section>';
    drawChart(document.getElementById('nw-chart'), history);
    bind(all);
  }
  function kpi(label, value, note, tone, ic) { return '<div class="nw-kpi"><p class="label">' + icon(ic) + esc(label) + '</p><p class="value' + (tone ? ' ' + tone : '') + '">' + esc(value) + '</p><p class="delta">' + esc(note) + '</p></div>'; }
  // Manual accounts whose newest balance is more than a quarter old; derived
  // series refresh with the statements and are not the owner's job.
  function staleAccounts(all) {
    return all.filter(function (s) { var p = latest(s); return s.manual && p && ageDays(p.date) > 90; }).map(function (s) { return s.account.name; });
  }
  function cpfNote(all) {
    var cpf = all.filter(function (s) { return s.account.group === 'cpf'; }), dates = cpf.map(latest).filter(Boolean).map(function (p) { return p.date; }).sort();
    if (!dates.length) return 'no CPF balance recorded';
    return 'as at ' + date(dates[dates.length - 1]) + (dates[0] !== dates[dates.length - 1] ? ' (oldest ' + date(dates[0]) + ')' : '');
  }
  function accountNote(account) {
    if (!account.note) return '';
    if (account.group === 'property' || account.note.length > 160) return '<details class="nw-account-notes"><summary>Details &amp; notes</summary><p>' + esc(account.note) + '</p></details>';
    return '<span class="nw-note">' + esc(account.note) + '</span>';
  }
  function sourceHTML(source) {
    if (!source) return '';
    return /^https?:\/\//i.test(source) ? '<a class="nw-note" href="' + esc(source) + '" target="_blank" rel="noopener noreferrer">Open source ↗</a>' : '<span class="nw-note">' + esc(source) + '</span>';
  }
  function groupHTML(g, series, subtotal) {
    var rows = series.map(function (s) {
      var p = latest(s), age = p ? ageDays(p.date) : null, stale = age != null && age > 90;
      var id = s.account.id, open = !!openHistory[id];
      return '<tr data-account="' + esc(id) + '"><td><span class="nw-name">' + esc(s.account.name) + '</span>' + accountNote(s.account) + '</td>' +
        '<td class="num">' + (p ? money(p.value) : '<span class="nw-none">No balance recorded</span>') + '</td>' +
        '<td>' + (p ? '<span class="' + (stale ? 'nw-stale' : 'nw-fresh') + '">' + date(p.date) + (age > 0 ? ' · ' + age + ' day' + (age === 1 ? '' : 's') + ' old' : '') + '</span>' + sourceHTML(p.source) : '<span class="nw-note">' + esc(s.account.source || '') + '</span>') + '</td>' +
        '<td class="act">' + (s.manual ? (editable ? '<button class="nw-button link" data-record="' + esc(id) + '">Record</button>' : '') + (s.points.length ? '<button class="nw-button link" data-history="' + esc(id) + '" aria-expanded="' + open + '">' + s.points.length + ' dated' + icon(open ? 'up' : 'down') + '</button>' : '') : '<span class="nw-muted">' + (s.sourceType === 'home' ? 'from Home register' : 'from statements') + '</span>') + '</td></tr>' +
        (open ? '<tr class="nw-history"><td colspan="4"><table>' + s.points.slice().reverse().map(function (q) { return '<tr><td>' + date(q.date) + '</td><td class="num">' + money(q.value) + '</td><td>' + esc(q.source || '') + (q.note ? ' · ' + esc(q.note) : '') + '</td><td class="act">' + (editable ? '<button class="nw-button link danger" data-delete="' + esc(id) + '" data-date="' + esc(q.date) + '">Remove</button>' : '') + '</td></tr>'; }).join('') + '</table></td></tr>' : '');
    }).join('');
    return '<section class="nw-group"><div class="nw-group-head"><h4><i class="nw-dot-' + g[0] + '"></i>' + esc(g[1]) + '</h4><strong>' + (subtotal == null ? '—' : money0(subtotal)) + '</strong></div>' +
      (rows ? '<table class="nw-table"><thead><tr><th>Account</th><th class="num">Balance</th><th>As at</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>' : '<p class="nw-empty">' + esc(g[2]) + '</p>') + '</section>';
  }
  function flowsHTML() {
    if (!flows.length) return '';
    return '<section class="nw-panel"><div class="nw-panel-head"><div><h3>Money moved from the bank account</h3><p class="nw-muted">Net of what came back, from the statements \u00b7 a reference when recording a broker balance, not a valuation</p></div></div><div class="nw-flows">' +
      flows.map(function (f) { return '<div class="nw-flow"><span>' + esc(f.name) + '</span><strong>' + money0(f.net) + '</strong><span>' + esc(f.detail) + '</span></div>'; }).join('') + '</div></section>';
  }

  // ---------- Chart ----------
  function drawChart(host, history) {
    if (history.length < 2) { host.innerHTML = '<p class="nw-chart-empty nw-muted">Record balances on at least two dates to see a history.</p>'; return; }
    var W = 900, H = 300, L = 64, R = 12, T = 14, B = 30, w = W - L - R, h = H - T - B;
    var order = ['cash', 'cpf', 'investments', 'insurance', 'property'];
    var maxA = Math.max.apply(null, history.map(function (r) { return r.assets; }).concat([1]));
    var maxL = Math.max.apply(null, history.map(function (r) { return r.liabilities; }).concat([0]));
    var step = niceStep((maxA + maxL) / 5), top = Math.ceil(maxA / step) * step, bottom = Math.ceil(maxL / step) * step;
    var y = function (v) { return T + (top - v) / (top + bottom) * h; }, x = function (i) { return L + i / (history.length - 1) * w; };
    var areas = '', stack = history.map(function () { return 0; });
    order.forEach(function (g) {
      if (!history.some(function (r) { return r.byGroup[g]; })) return;
      var upper = history.map(function (r, i) { return x(i) + ',' + y(stack[i] + (r.byGroup[g] || 0)); });
      var lower = history.map(function (r, i) { return x(i) + ',' + y(stack[i]); }).reverse();
      areas += '<polygon class="nw-area" data-group="' + g + '" points="' + upper.concat(lower).join(' ') + '"/>';
      history.forEach(function (r, i) { stack[i] += r.byGroup[g] || 0; });
    });
    if (maxL) {
      var up = history.map(function (r, i) { return x(i) + ',' + y(0); }), lo = history.map(function (r, i) { return x(i) + ',' + y(-r.liabilities); }).reverse();
      areas += '<polygon class="nw-area" data-group="liabilities" points="' + up.concat(lo).join(' ') + '"/>';
    }
    var net = history.map(function (r, i) { return (i ? 'L' : 'M') + x(i) + ' ' + y(r.net); }).join(' ');
    var ticks = '';
    for (var v = -bottom; v <= top; v += step) ticks += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '"/><text x="' + (L - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + compact(v) + '</text>';
    var labels = '', every = history.length > 30 ? 12 : history.length > 14 ? 6 : 3;
    history.forEach(function (r, i) { if (i % every === 0 || i === history.length - 1) labels += '<text x="' + x(i) + '" y="' + (H - 8) + '" text-anchor="middle">' + monthLabel(r.month) + '</text>'; });
    host.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Net worth by month"><g class="axis">' + ticks + labels + '</g>' + areas + '<path class="net" d="' + net + '"/><line class="hover" id="nw-hover" x1="0" x2="0" y1="' + T + '" y2="' + (T + h) + '" visibility="hidden"/></svg><div class="nw-tip" id="nw-tip" hidden></div>' +
      '<div class="nw-legend">' + order.concat(['liabilities']).filter(function (g) { return history.some(function (r) { return r.byGroup[g]; }); }).map(function (g) { return '<span><i class="nw-dot-' + g + '"></i>' + GROUP_NAME[g] + '</span>'; }).join('') + '<span><i class="net"></i>Net worth</span></div>';
    var svg = host.querySelector('svg'), tip = host.querySelector('#nw-tip'), line = host.querySelector('#nw-hover');
    svg.addEventListener('mousemove', function (e) {
      var rect = svg.getBoundingClientRect(), px = (e.clientX - rect.left) / rect.width * W;
      var i = Math.max(0, Math.min(history.length - 1, Math.round((px - L) / w * (history.length - 1)))), r = history[i];
      line.setAttribute('x1', x(i)); line.setAttribute('x2', x(i)); line.setAttribute('visibility', 'visible');
      tip.hidden = false;
      tip.innerHTML = '<strong>' + monthLabel(r.month) + '</strong>' + order.concat(['liabilities']).filter(function (g) { return r.byGroup[g]; }).map(function (g) { return '<div><span>' + GROUP_NAME[g] + '</span><span>' + (g === 'liabilities' ? '−' : '') + money0(r.byGroup[g]) + '</span></div>'; }).join('') + '<div><span><b>Net worth</b></span><span><b>' + money0(r.net) + '</b></span></div>';
      var left = (e.clientX - rect.left) / rect.width * host.clientWidth;
      tip.style.left = Math.min(left + 12, host.clientWidth - tip.offsetWidth - 4) + 'px'; tip.style.top = Math.max(0, (e.clientY - rect.top) - 10) + 'px';
    });
    svg.addEventListener('mouseleave', function () { tip.hidden = true; line.setAttribute('visibility', 'hidden'); });
  }
  function niceStep(raw) { var p = Math.pow(10, Math.floor(Math.log10(raw || 1))), n = raw / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }
  function compact(v) { var a = Math.abs(v); return (v < 0 ? '-' : '') + (a >= 1e6 ? (a / 1e6).toFixed(a % 1e6 ? 1 : 0) + 'M' : a >= 1e3 ? Math.round(a / 1e3) + 'k' : String(a)); }

  // ---------- Interaction ----------
  function bind(all) {
    root.querySelectorAll('[data-history]').forEach(function (b) { b.onclick = function () { openHistory[b.dataset.history] = !openHistory[b.dataset.history]; render(); }; });
    if (!editable) return;
    root.querySelectorAll('[data-record]').forEach(function (b) { b.onclick = function () { snapshotDialog(b.dataset.record); }; });
    root.querySelectorAll('[data-delete]').forEach(function (b) { b.onclick = function () { if (confirm('Remove the ' + date(b.dataset.date) + ' balance?')) save({ deleteSnapshot: { accountId: b.dataset.delete, date: b.dataset.date } }); }; });
    var add = root.querySelector('#nw-add-snapshot'); if (add) add.onclick = function () { snapshotDialog(''); };
    var acc = root.querySelector('#nw-add-account'); if (acc) acc.onclick = accountDialog;
  }
  function dialog(title, body, submitLabel, onSubmit) {
    document.querySelectorAll('dialog.nw-dialog').forEach(function (old) { old.remove(); });
    var d = document.createElement('dialog'); d.className = 'nw-dialog';
    d.innerHTML = '<form method="dialog" id="nw-form"><header class="nw-dialog-head"><h2>' + esc(title) + '</h2><button class="nw-button" type="button" id="nw-close" aria-label="Close">' + icon('close') + '</button></header><div class="nw-dialog-body">' + body + '<p id="nw-error" role="alert"></p></div><footer class="nw-dialog-foot"><span class="nw-muted">Saved locally with backups.</span><button class="nw-button primary" id="nw-save" type="submit">' + esc(submitLabel) + '</button></footer></form>';
    document.body.appendChild(d);
    d.querySelector('#nw-close').onclick = function () { if (!busy) d.close(); };
    d.addEventListener('close', function () { d.remove(); });
    d.addEventListener('cancel', function (e) { if (busy) e.preventDefault(); });
    d.querySelector('#nw-form').onsubmit = async function (e) {
      e.preventDefault(); if (busy) return;
      var values = {}; new FormData(e.target).forEach(function (v, k) { values[k] = v; });
      busy = true; d.querySelector('#nw-save').disabled = true;
      try { await save(onSubmit(values)); d.close(); }
      catch (err) { d.querySelector('#nw-error').textContent = err.message; }
      finally { busy = false; var s = d.querySelector('#nw-save'); if (s) s.disabled = false; }
    };
    d.showModal();
    return d;
  }
  function snapshotDialog(accountId) {
    var manual = store.accounts.filter(function (a) { return !a.archived; });
    if (!manual.length) { accountDialog(); return; }
    var chosen = manual.some(function (a) { return a.id === accountId; }) ? accountId : manual[0].id;
    var defaults = snapshotDefaults(manual, store.snapshots, chosen);
    var d = dialog('Record a balance',
      '<label class="nw-field">Account<select name="accountId">' + manual.map(function (a) { return '<option value="' + esc(a.id) + '" ' + (a.id === chosen ? 'selected' : '') + '>' + esc(GROUP_NAME[a.group] + ' · ' + a.name) + '</option>'; }).join('') + '</select></label>' +
      '<div class="nw-grid-2"><label class="nw-field">As at<input name="date" type="date" required value="' + today() + '" max="' + today() + '"></label><label class="nw-field">Balance (S$)<input name="value" type="number" step="0.01" min="0" required placeholder="' + esc(defaults.placeholder) + '"></label></div>' +
      '<label class="nw-field">Where you read it<input name="source" maxlength="300" placeholder="e.g. CPF portal, IBKR statement" value="' + esc(defaults.source) + '"></label>' +
      '<label class="nw-field">Note<textarea name="note" maxlength="1000"></textarea></label>',
      'Save balance', function (v) { return { snapshot: { accountId: v.accountId, date: v.date, value: Number(v.value), source: v.source || '', note: v.note || '' } }; });
    d.querySelector('[name=accountId]').onchange = function (e) {
      var next = snapshotDefaults(manual, store.snapshots, e.target.value);
      d.querySelector('[name=source]').value = next.source;
      d.querySelector('[name=value]').placeholder = next.placeholder;
    };
  }
  function accountDialog() {
    dialog('Add an account',
      '<label class="nw-field">Name<input name="name" required maxlength="300" placeholder="e.g. Interactive Brokers"></label>' +
      '<label class="nw-field">Group<select name="group">' + GROUPS.map(function (g) { return '<option value="' + g[0] + '">' + esc(g[1]) + '</option>'; }).join('') + '</select></label>' +
      '<label class="nw-field">Where balances come from<input name="source" maxlength="300" placeholder="e.g. broker portal, yearly statement"></label>' +
      '<label class="nw-field">Note<textarea name="note" maxlength="1000"></textarea></label>',
      'Add account', function (v) {
        var id = 'nw_' + v.name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'nw_account';
        var taken = store.accounts.some(function (a) { return a.id === id; }); if (taken) id += '_' + Date.now().toString(36);
        return { account: { id: id, name: v.name, group: v.group, source: v.source || '', note: v.note || '' } };
      });
  }
  async function save(change) {
    var body = Object.assign({ revision: store.revision }, change);
    var r = await fetch('api/net-worth', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    var saved = await r.json(); if (!r.ok || !saved.ok) throw new Error(saved.error || 'Save failed.');
    store = saved; render();
  }

  // ---------- Derived series ----------
  function deriveBank(bank) {
    var anchors = bank.statementAnchors || {}, months = (bank.months || []).slice().sort();
    var points = months.filter(function (m) { return anchors[m] && typeof anchors[m].closingBalance === 'number'; }).map(function (m) { return { date: monthEnd(m), value: anchors[m].closingBalance, source: 'Statement ' + (anchors[m].file || m) }; });
    if (!points.length) return null;
    return { account: { id: 'bank_uob_one', name: 'UOB ONE account', group: 'cash', note: 'Closing balance on each statement.' }, points: points, manual: false };
  }
  function deriveMortgage(home) {
    var loans = (home.records || []).filter(function (r) { return r.kind === 'mortgage' && r.status !== 'Archived' && typeof r.balance === 'number' && r.balanceDate; });
    return loans.map(function (r) { return { account: { id: 'home_' + r.id, name: r.name, group: 'liabilities', note: 'Balance from the Home register.' }, points: [{ date: r.balanceDate, value: r.balance, source: r.provider || 'Home register' }], sourceType: 'home', manual: false }; });
  }
  function deriveFlows(bank) {
    var buckets = {};
    (bank.transactions || []).forEach(function (t) {
      if (t.flow !== 'Investment' && t.flow !== 'Retirement (SRS)' && t.flow !== 'Fixed deposit') return;
      var d = (t.description || '').toUpperCase(), name = t.flow === 'Retirement (SRS)' ? 'SRS contributions' : t.flow === 'Fixed deposit' ? 'Fixed deposits' : /INTERACTIVE|IBKR/.test(d) ? 'Interactive Brokers' : /TIGER/.test(d) ? 'Tiger Brokers' : /PHILLIP/.test(d) ? 'Phillip Securities' : 'Other investments';
      var b = buckets[name] || (buckets[name] = { name: name, out: 0, back: 0, last: '' });
      if (t.direction === 'withdrawal') b.out += t.amount; else b.back += t.amount;
      if (t.date > b.last) b.last = t.date;
    });
    return Object.keys(buckets).map(function (k) { var b = buckets[k]; b.net = b.out - b.back; b.detail = money0(b.out) + ' out' + (b.back ? ', ' + money0(b.back) + ' back' : '') + ' · last ' + date(b.last); return b; }).sort(function (a, b) { return b.net - a.net; });
  }

  async function load() {
    try {
      var status = await json('api/status').catch(function () { return {}; });
      editable = !!(status.editable && status.netWorth);
      var results = await Promise.all([
        json('api/net-worth').catch(function () { return json('data/net_worth.json'); }).catch(function () { return { revision: 0, accounts: [], snapshots: [] }; }),
        json('data/account_transactions.json').catch(function () { return {}; }),
        json('data/home.json').catch(function () { return {}; })
      ]);
      store = results[0]; if (!Array.isArray(store.accounts) || !Array.isArray(store.snapshots)) throw new Error('Invalid net-worth records.');
      derived = [deriveBank(results[1])].filter(Boolean).concat(deriveMortgage(latestHome || results[2]));
      flows = deriveFlows(results[1]);
      render();
    } catch (error) { root.innerHTML = '<section class="nw-panel"><h2>Net worth unavailable</h2><p>' + esc(error.message) + '</p><button class="nw-button" id="nw-retry">Retry</button></section>'; root.querySelector('#nw-retry').onclick = load; }
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ageDays: ageDays, snapshotDefaults: snapshotDefaults, homeEquity: homeEquity, replaceHomeSeries: replaceHomeSeries };
    return;
  }
  var latestHome = null;
  window.addEventListener('finance:home-updated', function (e) {
    if (!e.detail || !Array.isArray(e.detail.records)) return;
    latestHome = e.detail;
    derived = replaceHomeSeries(derived, latestHome);
    if (started) render();
  });
  var started = false;
  function startVisible() {
    if (started || root.closest('.hidden')) return;
    started = true;
    root.innerHTML = '<p class="hint" role="status">Loading…</p>';
    load();
  }
  window.addEventListener('finance:navigation', startVisible);
  // The shell warms hidden tabs in idle time once the visible one is painted.
  window.addEventListener('finance:prewarm', function () {
    if (started || !document.getElementById('tab-wealth')) return;
    started = true;
    load();
  });
  startVisible();
}());
