(function () {
  "use strict";
  // Net worth: dated balance snapshots per account (manual/net_worth.json)
  // combined with series derived from data already on the dashboard - the
  // UOB ONE closing balance per statement month and, when the Home register
  // records a loan balance, the mortgage. Nothing here is a transaction.
  var root = typeof document !== 'undefined' ? document.getElementById('pane-networth') : null;
  if (!root && typeof module === 'undefined') return;
  if (typeof window !== 'undefined') window.NetWorthSchedule = { topUpDue: function () { return topUpDue.apply(null, arguments); } };
  var GROUPS = [
    ['cash', 'Cash', 'Bank balances from statements and any other cash accounts.'],
    ['cpf', 'CPF', 'Ordinary, Special and MediSave balances read from the CPF portal.'],
    ['investments', 'Investments', 'Brokerage, SRS and fixed deposits at their portal value.'],
    ['insurance', 'Insurance', 'Policies at net surrender or maturity value, not premiums paid.'],
    ['property', 'Property', 'Home at an estimated value; leave blank to keep it out of the total.'],
    ['liabilities', 'Liabilities', 'Loans and other amounts owed, subtracted from the total.']
  ];
  var GROUP_NAME = {}; GROUPS.forEach(function (g) { GROUP_NAME[g[0]] = g[1]; });
  var store = { revision: 0, accounts: [], snapshots: [] }, editable = false, busy = false, derived = [], flows = [], contributions = {}, openHistory = {};

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
  // An account topped up by a fixed amount in the same month every year
  // (`topUp: {month, amount, since}` on the record) carries that schedule as
  // balances: each top-up adds to the last known value once its month
  // begins. A balance recorded inside a top-up month is taken to include
  // that year's top-up, so a statement read after the transfer is not
  // counted twice.
  function scheduledPoints(account, recorded, now) {
    var t = account.topUp; if (!t) return recorded;
    var events = recorded.map(function (p) { return { date: p.date, point: p }; }), months = {};
    recorded.forEach(function (p) { months[monthOf(p.date)] = true; });
    for (var y = t.since; y <= +now.slice(0, 4); y++) {
      var m = y + '-' + String(t.month).padStart(2, '0'), first = m + '-01';
      if (first > now || months[m]) continue;
      events.push({ date: first, amount: t.amount });
    }
    events.sort(function (a, b) { return a.date.localeCompare(b.date); });
    var value = 0;
    return events.map(function (e) {
      if (e.point) { value = e.point.value; return e.point; }
      value = Math.round((value + e.amount) * 100) / 100;
      return { date: e.date, value: value, source: 'Scheduled top-up of ' + money0(e.amount), scheduled: true };
    });
  }
  // Reminder window for a yearly transfer ({month, amount, since} on an
  // account's `topUp` or a standalone entry in the register's `reminders`):
  // the month before it, its own month, and the month after (statements for
  // the transfer month arrive late). `seen` is the statement date of this
  // year's transfer when the entry names a statement `flow` or a `match`
  // text in the description, so the reminder clears itself.
  function topUpDue(name, t, now, transactions) {
    if (!t) return null;
    var year = +now.slice(0, 4), y = null, m, needle = t.match ? String(t.match).toUpperCase() : '';
    [year - 1, year].forEach(function (c) {
      var cm = c + '-' + String(t.month).padStart(2, '0');
      if (c >= t.since && now >= addMonths(cm, -1) + '-01' && now <= monthEnd(addMonths(cm, 1))) { y = c; m = cm; }
    });
    if (y == null) return null;
    var seen = null;
    (transactions || []).forEach(function (x) {
      if (x.direction !== 'withdrawal' || x.date.slice(0, 4) !== String(y)) return;
      var hit = (t.flow && x.flow === t.flow) || (needle && String(x.description || '').toUpperCase().indexOf(needle) !== -1);
      if (hit && (!seen || x.date > seen)) seen = x.date;
    });
    return { name: name, amount: t.amount, year: y, dueBy: monthEnd(m), overdue: now > monthEnd(m), seen: seen, tracked: !!(t.flow || needle) };
  }
  function nextTopUp(t, now) {
    var y = +now.slice(0, 4), m = String(t.month).padStart(2, '0');
    return (y + '-' + m + '-01' > now ? y : y + 1) + '-' + m;
  }
  function replaceHomeSeries(series, home) { return series.filter(function (s) { return s.sourceType !== 'home'; }).concat(deriveMortgage(home)); }
  var ICONS = { home: '<path d="m3 11 9-8 9 8v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>', close: '<path d="M6 6l12 12M18 6 6 18"/>', plus: '<path d="M12 5v14M5 12h14"/>', down: '<path d="m6 10 6 6 6-6"/>', up: '<path d="m6 14 6-6 6 6"/>', trend: '<path d="M3 17l5-5 4 4 9-9"/><path d="M15 7h6v6"/>', scale: '<path d="M12 3v18M5 7h14M7 7l-3 7a3 3 0 0 0 6 0L7 7Zm10 0-3 7a3 3 0 0 0 6 0l-3-7Z"/>' };
  function icon(name) { return '<svg class="nw-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICONS[name] + '</svg>'; }
  async function json(url) { if (window.FinanceData && url.indexOf('api/') !== 0) return window.FinanceData.load(url); var r = await fetch(url, { cache: 'no-store' }); if (!r.ok) throw new Error('Could not load ' + url); return r.json(); }

  // ---------- Series ----------
  // Every account becomes {account, points:[{date,value,source}]}; a value
  // carries forward from its snapshot date until the next snapshot.
  function allAccounts() {
    var manual = store.accounts.filter(function (a) { return !a.archived; }).map(function (a) {
      var points = store.snapshots.filter(function (s) { return s.accountId === a.id; }).slice().sort(function (x, y) { return x.date.localeCompare(y.date); });
      return { account: a, points: scheduledPoints(a, points, today()), manual: true };
    });
    // An account with no balance ever recorded is carried at its net
    // contributions from the statements, labelled as such in the register,
    // until the first portal value is entered.
    manual.forEach(function (s) {
      if (s.points.length) return;
      var name = (s.account.name || '').toLowerCase(), hit = null;
      Object.keys(contributions).forEach(function (k) {
        var first = k.toLowerCase().split(' ')[0];
        if (name.indexOf(first) !== -1 || k.toLowerCase().indexOf(name) !== -1) hit = contributions[k];
      });
      if (!hit || hit.total <= 0) return;
      s.points = hit.points.map(function (p) { return { date: p.date, value: p.value, source: p.source }; });
      s.estimated = true;
    });
    // A property recorded at its sales order sits in the books two years
    // before the keys and the loan. Counting it from the loan's start keeps
    // the asset and its debt together in the history; the register still
    // shows the original as-at date.
    manual.forEach(function (s) {
      var link = s.account.linkedRecord && derived.find(function (d) { return d.account.id === 'home_' + s.account.linkedRecord; });
      if (!link || !link.points.length || !s.points.length) return;
      var from = link.points[0].date;
      if (s.points[0].date >= from) return;
      var before = s.points.filter(function (p) { return p.date <= from; }), after = s.points.filter(function (p) { return p.date > from; });
      var basis = before[before.length - 1];
      s.points = [{ date: from, value: basis.value, source: basis.source, note: basis.note, recorded: basis.date }].concat(after);
    });
    return derived.concat(manual);
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
  // Change is the difference in total net worth between the two dates, the
  // way the headline figure would have moved. Accounts first recorded in
  // between (a new broker, a loan balance read for the first time) are named
  // so a jump can be traced to them rather than read as growth.
  function changeBetween(all, then, now) {
    var added = [], dropped = [];
    all.forEach(function (s) {
      var id = s.account.id, a = then.values[id], b = now.values[id];
      if (a == null && b != null) added.push(s.account.name);
      else if (a != null && b == null) dropped.push(s.account.name);
    });
    return { delta: now.net - then.net, base: then.net, added: added, dropped: dropped };
  }

  // ---------- Rendering ----------
  // Layout follows the net-worth trackers people know (Monarch, Copilot,
  // Empower): one headline number with its change and the history chart as
  // a single unit, then assets and liabilities side by side as grouped
  // lists, then the account register. Scope and range are view settings.
  // The property and its loan dwarf everything else on the chart and never
  // move between valuations, so the personal view is the default wherever a
  // property exists; the toggle brings the property in.
  var view = { scope: 'personal', range: 12 };
  function isHome(s) { return s.account.group === 'property' || s.sourceType === 'home'; }
  function render() {
    var everything = allAccounts();
    var hasHome = everything.some(isHome);
    if (!hasHome) view.scope = 'all';
    var all = view.scope === 'personal' ? everything.filter(function (s) { return !isHome(s); }) : everything;
    var months = monthRange(all), history = months.map(function (m) { return totalsFor(all, m); });
    var now = history.length ? history[history.length - 1] : { assets: 0, liabilities: 0, net: 0, byGroup: {}, values: {} };
    var oldest = null, newest = null;
    everything.forEach(function (s) { var p = latest(s); if (p) { if (!oldest || p.date < oldest) oldest = p.date; if (!newest || p.date > newest) newest = p.date; } });
    var shown = view.range && history.length > view.range ? history.slice(history.length - view.range - 1) : history;
    var back = shown.length > 1 ? shown[0] : null;
    var change = back ? changeBetween(all, back, now) : null;
    var span = back ? monthsBetween(back.month, now.month) : 0;
    var pct = change && change.base ? change.delta / Math.abs(change.base) * 100 : null;
    var latestNote = newest ? 'Latest balance ' + date(newest) + (oldest && oldest !== newest ? ' \u00b7 oldest ' + date(oldest) : '') : 'No balances recorded yet';
    var stale = staleAccounts(everything);
    var assetGroups = ['cash', 'cpf', 'investments', 'insurance', 'property'].filter(function (g) { return now.byGroup[g]; });
    var liabilityRows = all.filter(function (s) { return s.account.group === 'liabilities' && valueAt(s, now.month); });
    function seg(name, options) {
      return '<div class="nw-seg" role="group" aria-label="' + name + '">' + options.map(function (o) {
        return '<button type="button" class="nw-seg-btn' + (o.on ? ' active' : '') + '" aria-pressed="' + o.on + '" data-' + o.key + '="' + o.value + '">' + o.label + '</button>';
      }).join('') + '</div>';
    }
    root.innerHTML =
      '<section class="nw-panel nw-hero">' +
        '<div class="nw-hero-head"><div class="nw-hero-copy">' +
          '<p class="nw-eyebrow">Net worth' + (hasHome ? (view.scope === 'personal' ? ' \u00b7 personal accounts' : ' \u00b7 including property') : '') + '</p>' +
          '<strong class="nw-hero-value">' + money0(history.length ? now.net : null) + '</strong>' +
          '<p class="nw-hero-change">' + (change ? '<span class="' + (change.delta >= 0 ? 'good' : 'bad') + '">' + signed(change.delta) + (pct != null ? ' (' + (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%)' : '') + '</span> over ' + span + ' month' + (span === 1 ? '' : 's') + ' \u00b7 since ' + monthLabel(back.month) + changeNote(change) : '<span class="nw-muted">Record balances on two dates to see the change</span>') + '</p>' +
          '<p class="nw-muted">' + latestNote + (stale.length ? ' \u00b7 <span class="nw-stale">over 90 days old: ' + esc(stale.join(', ')) + '</span>' : '') + '</p>' +
        '</div><div class="nw-hero-controls">' +
          (hasHome ? seg('Scope', [{ key: 'scope', value: 'all', label: 'With property', on: view.scope === 'all' }, { key: 'scope', value: 'personal', label: 'Personal only', on: view.scope === 'personal' }]) : '') +
          seg('Range', [{ key: 'range', value: 12, label: '1Y', on: view.range === 12 }, { key: 'range', value: 36, label: '3Y', on: view.range === 36 }, { key: 'range', value: 0, label: 'All', on: !view.range }]) +
        '</div></div>' +
        '<div class="nw-chart" id="nw-chart"></div>' +
      '</section>' +
      '<div class="nw-split">' +
        '<section class="nw-panel nw-side"><div class="nw-side-head"><h3>Assets</h3><strong>' + money0(now.assets) + '</strong></div>' +
          (assetGroups.length ? assetGroups.map(function (g) {
            return '<a class="nw-side-row" href="#nw-group-' + g + '"><span class="nw-side-name"><i class="nw-dot-' + g + '"></i>' + GROUP_NAME[g] + '</span><span class="nw-side-bar"><span class="nw-side-fill" style="width:' + (now.assets ? (now.byGroup[g] / now.assets * 100).toFixed(1) : 0) + '%;background:var(--nw-' + g + ')"></span></span><span class="nw-side-value">' + money0(now.byGroup[g]) + '</span></a>';
          }).join('') : '<p class="nw-empty">No asset balances recorded.</p>') +
        '</section>' +
        '<section class="nw-panel nw-side"><div class="nw-side-head"><h3>Liabilities</h3><strong>' + money0(now.liabilities) + '</strong></div>' +
          (liabilityRows.length ? liabilityRows.map(function (s) {
            var v = valueAt(s, now.month).value;
            return '<a class="nw-side-row" href="#nw-group-liabilities"><span class="nw-side-name"><i class="nw-dot-liabilities"></i>' + esc(s.account.name) + '</span><span class="nw-side-bar"><span class="nw-side-fill" style="width:' + (now.liabilities ? (v / now.liabilities * 100).toFixed(1) : 0) + '%;background:var(--nw-liabilities)"></span></span><span class="nw-side-value">' + money0(v) + '</span></a>';
          }).join('') : '<p class="nw-empty">' + (view.scope === 'personal' && hasHome ? 'The home loan is set aside with the property.' : 'No liabilities recorded.') + '</p>') +
        '</section>' +
      '</div>' +
      '<details class="nw-about"><summary>How these figures are counted</summary><p>Assets minus liabilities, from balances you record here and the bank balances already read from statements. Each account holds its last recorded balance until the next one, so the as-at dates matter.' + (hasHome ? ' "Personal only" sets aside the property and the home loan but keeps other debts; the property is carried at acquisition cost, not a market valuation, with no ownership share applied.' : '') + ' The change is the movement in that total between the two dates, so an account whose first balance was recorded inside the window counts as growth from nothing; those accounts are named next to the change. An account with no balance recorded yet is carried at its net contributions from the statements (cost basis, marked in the register) until a portal value is entered.</p></details>' +
      flowsHTML() +
      '<section class="nw-panel"><div class="nw-panel-head"><div><h3>Balances</h3><p class="nw-muted">' + (editable ? 'Record a balance whenever you read one from a portal or statement.' : 'Read-only view.') + '</p></div><div class="nw-actions">' + (editable ? '<button class="nw-button" id="nw-add-account">' + icon('plus') + 'Add account</button><button class="nw-button primary" id="nw-add-snapshot">' + icon('plus') + 'Record balance</button>' : '') + '</div></div><div class="nw-groups">' + GROUPS.map(function (g) { return groupHTML(g, everything.filter(function (s) { return s.account.group === g[0]; }), totalsFor(everything, now.month || monthOf(today())).byGroup[g[0]]); }).join('') + '</div></section>';
    drawChart(document.getElementById('nw-chart'), shown);
    root.querySelectorAll('[data-scope]').forEach(function (b) { b.onclick = function () { view.scope = b.dataset.scope; render(); }; });
    root.querySelectorAll('[data-range]').forEach(function (b) { b.onclick = function () { view.range = parseInt(b.dataset.range, 10) || 0; render(); }; });
    bind(everything);
  }
  // Accounts that entered or left the books inside the comparison window.
  function changeNote(change) {
    function part(names, verb) {
      if (!names.length) return '';
      return ' \u00b7 <span class="nw-muted" title="' + esc(names.join(', ')) + '">' + (names.length <= 2 ? esc(names.join(' and ')) + ' ' + verb : names.length + ' accounts ' + verb) + '</span>';
    }
    return part(change.added, 'added since') + part(change.dropped, 'no longer counted');
  }
  // Manual accounts whose newest balance is more than a quarter old; derived
  // series refresh with the statements and are not the owner's job.
  function staleAccounts(all) {
    return all.filter(function (s) { var p = latest(s); return s.manual && !s.account.topUp && p && ageDays(p.date) > 90; }).map(function (s) { return s.account.name; });
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
      var p = latest(s), age = p ? ageDays(p.date) : null, stale = age != null && age > 90 && !s.account.topUp;
      var id = s.account.id, open = !!openHistory[id];
      return '<tr data-account="' + esc(id) + '"><td><span class="nw-name">' + esc(s.account.name) + '</span>' + accountNote(s.account) + '</td>' +
        '<td class="num">' + (p ? money(p.value) : '<span class="nw-none">No balance recorded</span>') + '</td>' +
        '<td>' + (p ? '<span class="' + (stale ? 'nw-stale' : 'nw-fresh') + '">' + date(p.date) + (age > 0 ? ' · ' + age + ' day' + (age === 1 ? '' : 's') + ' old' : '') + '</span>' + sourceHTML(p.source) : '<span class="nw-note">' + esc(s.account.source || '') + '</span>') + (s.account.topUp ? '<span class="nw-note">Next top-up ' + monthLabel(nextTopUp(s.account.topUp, today())) + '</span>' : '') + '</td>' +
        '<td class="act">' + (s.manual ? (editable ? '<button class="nw-button link" data-record="' + esc(id) + '">Record</button>' : '') + (s.estimated ? '<span class="nw-muted" title="Cost basis: what has been transferred in from UOB ONE, net of withdrawals. Record a portal value to replace it.">cost basis</span>' : s.points.length ? '<button class="nw-button link" data-history="' + esc(id) + '" aria-expanded="' + open + '">' + s.points.length + ' dated' + icon(open ? 'up' : 'down') + '</button>' : '') : '<span class="nw-muted">' + (s.sourceType === 'home' ? 'from Home register' : 'from statements') + '</span>') + '</td></tr>' +
        (open ? '<tr class="nw-history"><td colspan="4"><table>' + s.points.slice().reverse().map(function (q) { return '<tr><td>' + date(q.date) + '</td><td class="num">' + money(q.value) + '</td><td>' + esc(q.source || '') + (q.note ? ' · ' + esc(q.note) : '') + '</td><td class="act">' + (editable ? '<button class="nw-button link danger" data-delete="' + esc(id) + '" data-date="' + esc(q.date) + '">Remove</button>' : '') + '</td></tr>'; }).join('') + '</table></td></tr>' : '');
    }).join('');
    return '<section class="nw-group" id="nw-group-' + esc(g[0]) + '"><div class="nw-group-head"><h4><i class="nw-dot-' + g[0] + '"></i>' + esc(g[1]) + '</h4><strong>' + (subtotal == null ? '—' : money0(subtotal)) + '</strong></div>' +
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
    // The steadiest, largest band sits at the base so its edge reads flat and
    // the moving bands above it stay readable.
    var order = ['property', 'cpf', 'cash', 'investments', 'insurance'];
    var maxA = Math.max.apply(null, history.map(function (r) { return r.assets; }).concat([1]));
    var maxL = Math.max.apply(null, history.map(function (r) { return r.liabilities; }).concat([0]));
    var step = niceStep((maxA + maxL) / 5), top = Math.ceil(maxA / step) * step, bottom = Math.ceil(maxL / step) * step;
    // Balances are read on a handful of dates and carried forward, so the
    // series are drawn as steps: each month holds its value across its own
    // column, and a change shows where it was recorded rather than as a ramp
    // implying steady movement in between.
    var n = history.length, col = w / n;
    var y = function (v) { return T + (top - v) / (top + bottom) * h; }, x = function (i) { return L + i * col; }, mid = function (i) { return x(i) + col / 2; };
    function steps(valueAt) {
      var pts = [];
      history.forEach(function (r, i) { var py = y(valueAt(r, i)); pts.push(x(i) + ',' + py, x(i + 1) + ',' + py); });
      return pts;
    }
    var areas = '', stack = history.map(function () { return 0; });
    order.forEach(function (g) {
      if (!history.some(function (r) { return r.byGroup[g]; })) return;
      var upper = steps(function (r, i) { return stack[i] + (r.byGroup[g] || 0); });
      var lower = steps(function (r, i) { return stack[i]; }).reverse();
      areas += '<polygon class="nw-area" data-group="' + g + '" points="' + upper.concat(lower).join(' ') + '"/>';
      history.forEach(function (r, i) { stack[i] += r.byGroup[g] || 0; });
    });
    if (maxL) {
      var up = steps(function () { return 0; }), lo = steps(function (r) { return -r.liabilities; }).reverse();
      areas += '<polygon class="nw-area" data-group="liabilities" points="' + up.concat(lo).join(' ') + '"/>';
    }
    var net = steps(function (r) { return r.net; }).map(function (p, i) { return (i ? 'L' : 'M') + p.replace(',', ' '); }).join(' ');
    var ticks = '';
    for (var v = -bottom; v <= top; v += step) ticks += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + y(v) + '" y2="' + y(v) + '"/><text x="' + (L - 8) + '" y="' + (y(v) + 4) + '" text-anchor="end">' + compact(v) + '</text>';
    var labels = '', every = n > 30 ? 12 : n > 14 ? 6 : 3;
    history.forEach(function (r, i) { if (i % every === 0 || i === n - 1) labels += '<text x="' + mid(i) + '" y="' + (H - 8) + '" text-anchor="middle">' + monthLabel(r.month) + '</text>'; });
    host.innerHTML = '<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Net worth by month"><g class="axis">' + ticks + labels + '</g>' + areas + '<path class="net-halo" d="' + net + '"/><path class="net" d="' + net + '"/><line class="hover" id="nw-hover" x1="0" x2="0" y1="' + T + '" y2="' + (T + h) + '" visibility="hidden"/></svg><div class="nw-tip" id="nw-tip" hidden></div>' +
      '<div class="nw-legend">' + order.concat(['liabilities']).filter(function (g) { return history.some(function (r) { return r.byGroup[g]; }); }).map(function (g) { return '<span class="nw-legend-item" tabindex="0" data-group="' + g + '"><i class="nw-dot-' + g + '"></i>' + GROUP_NAME[g] + '</span>'; }).join('') + '<span class="nw-legend-item" tabindex="0" data-group="net"><i class="net"></i>Net worth</span></div>';
    var svg = host.querySelector('svg'), tip = host.querySelector('#nw-tip'), line = host.querySelector('#nw-hover');
    svg.addEventListener('mousemove', function (e) {
      var rect = svg.getBoundingClientRect(), px = (e.clientX - rect.left) / rect.width * W;
      var i = Math.max(0, Math.min(n - 1, Math.floor((px - L) / col))), r = history[i];
      line.setAttribute('x1', mid(i)); line.setAttribute('x2', mid(i)); line.setAttribute('visibility', 'visible');
      tip.hidden = false;
      tip.innerHTML = '<strong>' + monthLabel(r.month) + '</strong>' + order.concat(['liabilities']).filter(function (g) { return r.byGroup[g]; }).map(function (g) { return '<div><span>' + GROUP_NAME[g] + '</span><span>' + (g === 'liabilities' ? '−' : '') + money0(r.byGroup[g]) + '</span></div>'; }).join('') + '<div><span><b>Net worth</b></span><span><b>' + money0(r.net) + '</b></span></div>';
      var left = (e.clientX - rect.left) / rect.width * host.clientWidth;
      tip.style.left = Math.min(left + 12, host.clientWidth - tip.offsetWidth - 4) + 'px'; tip.style.top = Math.max(0, (e.clientY - rect.top) - 10) + 'px';
    });
    svg.addEventListener('mouseleave', function () { tip.hidden = true; line.setAttribute('visibility', 'hidden'); });
    // Hovering or focusing a legend entry lifts that series and dims the rest.
    var areas = host.querySelectorAll('.nw-area'), netPath = host.querySelector('.net');
    function focusGroup(g) {
      areas.forEach(function (a) { a.classList.toggle('is-dim', !!g && a.dataset.group !== g); a.classList.toggle('is-focus', a.dataset.group === g); });
      netPath.classList.toggle('is-dim', !!g && g !== 'net'); netPath.classList.toggle('is-focus', g === 'net');
    }
    host.querySelectorAll('.nw-legend-item').forEach(function (item) {
      var on = function () { focusGroup(item.dataset.group); }, off = function () { focusGroup(null); };
      item.addEventListener('mouseenter', on); item.addEventListener('focus', on);
      item.addEventListener('mouseleave', off); item.addEventListener('blur', off);
    });
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
    return loans.map(function (r) {
      var points = [{ date: r.balanceDate, value: r.balance, source: r.provider || 'Home register' }];
      // A loan exists from its first disbursement, and it shrinks by the
      // principal in every instalment. Working back from the recorded balance
      // with the instalment and rate on the record gives each earlier month's
      // balance; without those the balance is simply carried back.
      if (r.starts && r.starts < r.balanceDate) {
        var monthly = typeof r.rate === 'number' ? r.rate / 100 / 12 : 0;
        var instalment = typeof r.instalment === 'number' ? r.instalment : 0;
        var how = instalment ? 'amortised back from the ' + date(r.balanceDate) + ' balance at ' + money0(instalment) + ' a month' + (r.rate ? ' and ' + r.rate + '%' : '') : 'carried back from the ' + date(r.balanceDate) + ' balance';
        var m = monthOf(r.balanceDate), startMonth = monthOf(r.starts), value = r.balance;
        function stepBack() { value = instalment ? (value + instalment) / (1 + monthly) : value; return Math.round(value * 100) / 100; }
        while (m > startMonth) {
          m = addMonths(m, -1);
          points.unshift({ date: m === startMonth ? r.starts : monthEnd(m), value: stepBack(), source: how, carried: true });
        }
        if (points[0].date !== r.starts) points.unshift({ date: r.starts, value: stepBack(), source: how, carried: true });
      }
      return { account: { id: 'home_' + r.id, name: r.name, group: 'liabilities', note: 'Balance from the Home register.' }, points: points, sourceType: 'home', manual: false }; });
  }
  function flowBucket(t) {
    if (t.flow !== 'Investment' && t.flow !== 'Retirement (SRS)' && t.flow !== 'Fixed deposit') return null;
    var d = (t.description || '').toUpperCase();
    return t.flow === 'Retirement (SRS)' ? 'SRS contributions' : t.flow === 'Fixed deposit' ? 'Fixed deposits' : /INTERACTIVE|IBKR/.test(d) ? 'Interactive Brokers' : /TIGER/.test(d) ? 'Tiger Brokers' : /PHILLIP/.test(d) ? 'Phillip Securities' : 'Other investments';
  }
  // Cumulative net transfers per destination, one point per month with a
  // transfer: the cost basis of an account whose portal value has not been
  // recorded. Owner decision 12 Sep 2026: Interactive Brokers is carried at
  // this basis until a portal balance is entered, which then takes over.
  function contributionSeries(bank) {
    var byName = {};
    (bank.transactions || []).slice().sort(function (a, b) { return a.date.localeCompare(b.date); }).forEach(function (t) {
      var name = flowBucket(t); if (!name) return;
      var run = byName[name] || (byName[name] = { name: name, total: 0, points: [] });
      run.total += t.direction === 'withdrawal' ? t.amount : -t.amount;
      var m = monthOf(t.date), last = run.points[run.points.length - 1];
      var value = Math.round(run.total * 100) / 100;
      if (last && monthOf(last.date) === m) last.value = value;
      else run.points.push({ date: monthEnd(m), value: value, source: 'net contributions from UOB ONE statements' });
    });
    return byName;
  }
  function deriveFlows(bank) {
    var buckets = {};
    (bank.transactions || []).forEach(function (t) {
      var name = flowBucket(t); if (!name) return;
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
      contributions = contributionSeries(results[1]);
      render();
    } catch (error) { root.innerHTML = '<section class="nw-panel"><h2>Net worth unavailable</h2><p>' + esc(error.message) + '</p><button class="nw-button" id="nw-retry">Retry</button></section>'; root.querySelector('#nw-retry').onclick = load; }
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ageDays: ageDays, snapshotDefaults: snapshotDefaults, homeEquity: homeEquity, replaceHomeSeries: replaceHomeSeries, contributionSeries: contributionSeries, changeBetween: changeBetween, scheduledPoints: scheduledPoints, nextTopUp: nextTopUp, topUpDue: topUpDue };
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
