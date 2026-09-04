(function () {
  "use strict";

  var EXCLUDED = { Payment: true, Rebates: true };
  // Owner sources that need no further review: tagged by you, or a merchant rule
  // signed off under "confirmed" in manual/owner_rules.json.
  var SETTLED = { "exact-id": 1, "exact-legacy": 1, "merchant-rule-confirmed": 1 };
  var WEALTH = { "Investment": 1, "Retirement (SRS)": 1, "Fixed deposit": 1 };
  var MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var OWNER_ORDER = ["Nic", "Shared", "Yx", "Untagged"];
  var CATEGORY_ORDER = [
    "Food & dining", "Transport", "Wallet funding", "Shopping", "Groceries", "Games",
    "Subscriptions", "Insurance", "Healthcare", "Home & furnishings",
    "Entertainment", "Sports & fitness", "Personal care", "Pet care",
    "Travel", "Bills & utilities", "Work", "Fees & charges", "Investment",
    "Retirement (SRS)", "Fixed deposit", "Payment", "Rebates", "Other"
  ];
  var LEDGER_CAP = 400;
  // Ids per /api/account-review request until /api/status says otherwise.
  var ACCOUNT_REVIEW_BATCH_LIMIT = 100;

  var data = null;
  var account = { transactions: [], months: [] };
  var accountReviewedSignals = {};
  // Handle returned by bindToggle, so a drill-down can reset the grouping
  // toggle's own closure state and not just the flag on `state`.
  var groupToggle = null;
  var editor = {
    available: false,
    accountReviewBatchLimit: ACCOUNT_REVIEW_BATCH_LIMIT,
    toastTimer: null,
    toastHideTimer: null,
    drawerTransactionId: null,
    drawerLastFocus: null,
    drawerCloseTimer: null,
    auditOpen: false,
    auditLastFocus: null,
    auditCloseTimer: null
  };
  var state = {
    tab: "overview",
    month: null,
    splitYear: null,
    gameYear: null,
    game: "All",
    insurancePerson: "nick",
    insuranceYear: null,
    foodpandaOnly: false,
    shopeeOnly: false,
    grabOnly: false,
    range: "6",
    series: { income: true, spent: true, invested: true },
    search: "",
    transactionSource: "card",
    owner: "All",
    category: "All",
    bankDirection: "all",
    bankReview: "all",
    bankExcludeInternal: false,
    showExcluded: false,
    groupPurchases: false,
    reviewMode: null,
    // Exact id set behind an insight drill-down, so the rows opened are the
    // same rows the insight counted. null means no id filter is active.
    idFilter: null,
    idFilterLabel: "",
    idFilterCount: 0,
    ledgerLimit: LEDGER_CAP,
    period: { mode: "month", year: null, month: null }
  };

  function fmt(n, digits) {
    var d = digits === undefined ? 2 : digits;
    return (n < 0 ? "-" : "") + "S$" + Math.abs(n).toLocaleString("en-SG",
      { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fmt0(n) { return fmt(n, 0); }
  function roundMoney(n) {
    var sign = n < 0 ? -1 : 1;
    return sign * Math.round(Math.abs(n) * 100 + 1e-9) / 100;
  }
  function monthLabel(key) {
    var p = key.split("-");
    return MONTH_NAMES[parseInt(p[1], 10) - 1] + " " + p[0];
  }
  function dateLabel(value) {
    if (!value) return "unknown date";
    var p = value.slice(0, 10).split("-");
    return String(parseInt(p[2], 10)) + " " +
      MONTH_NAMES[parseInt(p[1], 10) - 1] + " " + p[0];
  }
  function localDate(value) {
    var p = value.slice(0, 10).split("-");
    return new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
  }
  function dayDifference(left, right) {
    return Math.floor((left.getTime() - right.getTime()) / 86400000);
  }
  function shortMonthLabel(key) {
    var p = key.split("-");
    var m = MONTH_NAMES[parseInt(p[1], 10) - 1];
    return p[1] === "01" ? m + " '" + p[0].slice(2) : m;
  }
  function shortDate(date, includeYear) {
    var p = date.split("-");
    var out = String(parseInt(p[2], 10)) + " " + MONTH_NAMES[parseInt(p[1], 10) - 1];
    return includeYear ? out + " " + p[0] : out;
  }
  function statementRange(month) {
    var dates = data.transactions.filter(function (t) {
      return t.month === month && t.date;
    }).map(function (t) { return t.date; }).sort();
    if (!dates.length) return "";
    var first = dates[0], last = dates[dates.length - 1];
    if (first === last) return shortDate(first, true);
    var crossYear = first.slice(0, 4) !== last.slice(0, 4);
    if (first.slice(0, 7) === last.slice(0, 7)) {
      return String(parseInt(first.slice(8), 10)) + "–" + shortDate(last, true);
    }
    return shortDate(first, crossYear) + "–" + shortDate(last, true);
  }
  function statementLabel(month) {
    return monthLabel(month) + " statement";
  }
  function setPressed(button, pressed) {
    button.setAttribute("aria-pressed", pressed ? "true" : "false");
  }
  function catClass(category) {
    return "cat-" + category.toLowerCase().replace(/[^a-z]+/g, "-").replace(/^-|-$/g, "");
  }
  function transactionName(t) {
    return t.displayName || (t.foodpanda && t.foodpanda.merchant) ||
      (t.shopee && t.shopee.merchant) || grabTransactionName(t) || t.description;
  }
  function grabReceiptName(receipt) {
    if (!receipt) return "";
    if ((receipt.category === "Food & dining" || receipt.category === "Groceries") &&
        receipt.merchant && receipt.merchant !== "Grab") return receipt.merchant;
    if (receipt.pickup && receipt.dropoff) {
      return (receipt.pickupLabel || receipt.pickup) + " → " +
        (receipt.dropoffLabel || receipt.dropoff);
    }
    if (receipt.merchant && receipt.merchant !== "Grab") return receipt.merchant;
    return receipt.service || "Grab";
  }
  function grabTransactionName(t) {
    if (!t.grab || !Array.isArray(t.grab.receipts)) return "";
    return t.grab.receipts.map(grabReceiptName).filter(Boolean).join(" · ");
  }
  var MERCHANT_LOGOS = [
    { name: "Grab", src: "assets/merchant-logos/grab.ico", pattern: /(^|\s)GRAB(?:\*|\s|-|$)/ },
    { name: "Foodpanda", src: "assets/merchant-logos/foodpanda.png", pattern: /FOOD\s?PANDA|FP\*FOOD/ },
    { name: "Shopee", src: "assets/merchant-logos/shopee.png", pattern: /(^|\s)SHOPEE(?:PAY)?(?:\s|\*|-|$)/ },
    { name: "FairPrice", src: "assets/merchant-logos/fairprice.png", pattern: /(?:NTUC\s+)?FAIRPRICE|NTUC\s+FP(?:\s|-|$)/ },
    { name: "Netflix", src: "assets/merchant-logos/netflix.png", pattern: /(^|\s)NETFLIX(?:\s|\*|\.|$)/ },
    { name: "Spotify", src: "assets/merchant-logos/spotify.png", pattern: /(^|\s)SPOTIFY(?:\s|\*|\.|$)/ },
    { name: "ActiveSG", src: "assets/merchant-logos/activesg.ico", pattern: /MYACTIVESG|(^|\s)ACTIVESG(?:\s|$)/ },
    { name: "HoYoverse", src: "assets/merchant-logos/hoyoverse.ico", pattern: /HOYOVERSE|COGNOSPHERE/ },
    { name: "Apple", src: "assets/merchant-logos/apple.ico", pattern: /APPLE\.COM\/BILL/ },
    { name: "Prudential", src: "assets/merchant-logos/prudential.ico", pattern: /(^|\s)PRUDENTIAL(?:\s|$)/ },
    { name: "giga!", src: "assets/merchant-logos/giga.ico", pattern: /(^|\s)GIGA(?:!|\s|$)/ },
    { name: "McDonald's", src: "assets/merchant-logos/mcdonalds.svg", pattern: /MCDONALD/ },
    { name: "Starbucks", src: "assets/merchant-logos/starbucks.png", pattern: /STARBUCKS/ },
    { name: "Deliveroo", src: "assets/merchant-logos/deliveroo.png", pattern: /DELIVEROO/ },
    { name: "Trip.com", src: "assets/merchant-logos/trip.png", pattern: /TRIP\.COM/ },
    { name: "ShopBack", src: "assets/merchant-logos/shopback.ico", pattern: /SHOPBACK/ },
    { name: "The Coffee Bean", src: "assets/merchant-logos/coffeebean.png", pattern: /COFFEE\s+BEAN/ },
    { name: "Singapore public transport", src: "assets/merchant-logos/singapore-transit.svg", pattern: /BUS[\/\s-]*MRT/ }
  ];
  function merchantLogo(value) {
    var t = value && typeof value === "object" ? value : null;
    if (t && t.grab) return MERCHANT_LOGOS[0];
    if (t && t.foodpanda) return MERCHANT_LOGOS[1];
    if (t && t.shopee) return MERCHANT_LOGOS[2];
    var text = String(t
      ? [t.description, t.displayName, transactionName(t)].filter(Boolean).join(" ")
      : value || "").toUpperCase();
    return MERCHANT_LOGOS.find(function (logo) { return logo.pattern.test(text); }) || null;
  }
  function addMerchantLogo(container, value) {
    var logo = merchantLogo(value);
    if (!logo) return;
    var image = el("img", "merchant-logo");
    image.src = logo.src;
    image.alt = "";
    image.setAttribute("aria-hidden", "true");
    image.loading = "lazy";
    image.decoding = "async";
    container.classList.add("has-merchant-logo");
    container.insertBefore(image, container.firstChild);
  }
  // Known accounts and trusted counterparties live in the generated data file,
  // not in the source, so they have to be pushed into the grouping module every
  // time `data` is replaced - before anything reads a counterparty from it. An
  // older file without an "identity" key simply configures nothing.
  function applyIdentity() {
    window.FinanceGrouping.configure((data && data.identity) || {});
  }
  function refreshAccountAnalysis() {
    var analysis = window.FinanceGrouping.analyzeAccountTransactions(
      account.transactions, accountReviewedSignals);
    account.transactions.forEach(function (transaction) {
      transaction.accountReview = analysis[transaction.id] || {
        counterparty: window.FinanceGrouping.accountCounterparty(transaction.description),
        reasons: [], checks: [], requiresReview: false, reviewed: false
      };
      transaction.counterparty = transaction.accountReview.counterparty;
    });
  }
  function categoryColor(category) {
    return {
      "Food & dining": "var(--cat-food)",
      "Transport": "var(--cat-transport)",
      "Shopping": "var(--cat-shopping)",
      "Games": "var(--cat-games)",
      "Subscriptions": "var(--cat-subscriptions)",
      "Groceries": "var(--cat-groceries)",
      "Insurance": "var(--cat-insurance)",
      "Healthcare": "var(--cat-healthcare)",
      "Travel": "var(--cat-travel)",
      "Wallet funding": "var(--text-3)",
      "Work": "var(--cat-work)"
    }[category] || "var(--accent)";
  }
  function signed(t) { return t.type === "debit" ? t.amount : -t.amount; }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function reducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  function animateNumber(node) {
    var finalText = node.textContent;
    node.setAttribute("aria-label", finalText);
    if (reducedMotion()) return;
    var match = finalText.match(/^([+-]?)(S\$)([\d,]+)(?:\.(\d+))?$/);
    if (!match) return;
    var sign = match[1];
    var target = parseFloat(match[3].replace(/,/g, "") + (match[4] ? "." + match[4] : ""));
    var digits = match[4] ? match[4].length : 0;
    var started = null;
    node.classList.add("number-animating");
    function frame(now) {
      if (started === null) started = now;
      var progress = Math.min(1, (now - started) / 380);
      var eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = sign + "S$" + (target * eased).toLocaleString("en-SG", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits
      });
      if (progress < 1) {
        window.requestAnimationFrame(frame);
      } else {
        node.textContent = finalText;
        node.classList.remove("number-animating");
      }
    }
    window.requestAnimationFrame(frame);
  }
  function animateCount(node) {
    var finalText = node.textContent;
    node.setAttribute("aria-label", finalText);
    if (reducedMotion() || !/^\d+$/.test(finalText)) return;
    var target = parseInt(finalText, 10);
    var started = null;
    node.classList.add("number-animating");
    function frame(now) {
      if (started === null) started = now;
      var progress = Math.min(1, (now - started) / 320);
      var eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = String(Math.round(target * eased));
      if (progress < 1) {
        window.requestAnimationFrame(frame);
      } else {
        node.textContent = finalText;
        node.classList.remove("number-animating");
      }
    }
    window.requestAnimationFrame(frame);
  }
  function showToast(message, tone) {
    var toast = document.getElementById("toast");
    toast.textContent = message;
    toast.className = "toast " + (tone || "success");
    if (editor.toastTimer) window.clearTimeout(editor.toastTimer);
    if (editor.toastHideTimer) window.clearTimeout(editor.toastHideTimer);
    editor.toastTimer = window.setTimeout(function () {
      toast.classList.add("leaving");
      editor.toastHideTimer = window.setTimeout(function () {
        toast.classList.add("hidden");
      }, 220);
    }, 4000);
  }
  function ownerSourceLabel(source) {
    return {
      "exact-id": "confirmed by stable ID",
      "exact-legacy": "legacy exact tag",
      "merchant-rule": "merchant rule, unconfirmed",
      "merchant-rule-confirmed": "merchant rule you confirmed",
      "unassigned": "not assigned"
    }[source] || "unknown";
  }
  function normalizeRemark(value) {
    return value.trim().replace(/\s+/g, " ");
  }

  // ---------- Applying a save without reloading the data file ----------
  //
  // Every write endpoint returns what it changed, so a save patches the rows it
  // names and repaints the panels those fields feed. Refetching the generated
  // data file (megabytes) and re-rendering every tab to move one owner tag made
  // tagging a list of rows feel like a page load.
  //
  // The refetch stays as the single fallback: a response that does not carry the
  // expected fields, or names rows this page does not hold, is treated as a
  // reason to reload rather than to guess.
  function refetchAfterSave() {
    return loadJson("data/transactions.json?updated=" + Date.now())
      .then(function (fresh) {
        data = fresh;
        applyIdentity();
        renderAll();
      });
  }
  // Owner, category, display name, remark and review decisions reach the quality
  // panel, the ledger and its summary, the settlement, the insights and the two
  // overview totals - and nothing else on the page.
  function renderSavedRowEffects(rebuilt) {
    if (rebuilt) {
      // Only a whole-row save can change a category, which may retire one from
      // the filter, and moving a row into or out of Payment/Rebates changes the
      // headline card figures too.
      populateTransactionCategoryFilter();
      renderKpis();
    }
    renderDataQuality();
    renderLedger();
    renderSplit();
    renderInsights();
    renderSpendingSummary();
    renderKeyMetrics();
    renderCategories();
    renderInsurance();
  }
  function reopenDrawerFor(id) {
    if (editor.drawerTransactionId !== id) return;
    var updated = data.transactions.find(function (row) { return row.id === id; });
    if (updated) openTransactionDrawer(updated);
  }
  function applySavedRows(payload) {
    if (!payload || typeof payload !== "object") return false;
    var rows = [];
    if (Array.isArray(payload.transactions)) rows = rows.concat(payload.transactions);
    if (payload.transaction) rows.push(payload.transaction);
    rows = rows.filter(function (row) { return row && row.id; });
    var risk = payload.risk && payload.risk.key ? payload.risk : null;
    if (!rows.length && !risk) return false;

    var byId = {};
    data.transactions.forEach(function (row) { byId[row.id] = row; });
    var matched = 0;
    var rebuilt = false;
    rows.forEach(function (fields) {
      var row = byId[fields.id];
      if (!row) return;
      matched += 1;
      // A rebuilt row replaces the held one key for key, so a display name or
      // remark you cleared disappears instead of lingering. A partial response
      // (owner, remark) patches only the fields it names. Either way the row
      // object itself is kept, so open closures still point at live data.
      if (fields.month !== undefined && fields.amount !== undefined) {
        rebuilt = true;
        Object.keys(row).forEach(function (key) {
          if (!Object.prototype.hasOwnProperty.call(fields, key)) delete row[key];
        });
      }
      Object.keys(fields).forEach(function (key) { row[key] = fields[key]; });
    });
    if (risk) {
      // A review decision is recorded against the signal, not the row, so every
      // row carrying that key follows it. `primary` is that row's own place in
      // the group and must survive, or the review queue loses its listed row.
      data.transactions.forEach(function (row) {
        if (!row.risk || row.risk.key !== risk.key) return;
        row.risk.recognized = risk.recognized === true;
        matched += 1;
      });
    }
    if (!matched) return false;
    if (payload.quality) data.quality = payload.quality;
    renderSavedRowEffects(rebuilt);
    return true;
  }
  function saveRemark(t, remark, input) {
    input.disabled = true;
    fetch("api/remark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: t.id, remark: remark })
    }).then(function (response) {
      return response.json().catch(function () {
        return { error: "The local server returned an unreadable response." };
      }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "Remark save failed.");
        return payload;
      });
    }).then(function (payload) {
      if (!applySavedRows(payload)) return refetchAfterSave();
    }).then(function () {
      setTab("transactions");
      showToast(remark ? "Remark saved." : "Remark removed.", "success");
    }).catch(function (error) {
      input.disabled = false;
      showToast(error.message, "error");
    });
  }
  function buildRemarkInput(t) {
    var input = document.createElement("input");
    var original = t.remark || "";
    input.type = "text";
    input.className = "remark-input";
    input.maxLength = 240;
    input.placeholder = "Add remark";
    input.setAttribute("aria-label", "Remark for " + t.description);
    input.value = original;
    input.disabled = !editor.available;
    if (!editor.available) {
      input.title = "Start scripts/serve.py to enable editing.";
    }
    function commit() {
      var remark = normalizeRemark(input.value);
      input.value = remark;
      if (input.disabled || remark === original) return;
      saveRemark(t, remark, input);
    }
    input.addEventListener("click", function (event) {
      event.stopPropagation();
    });
    input.addEventListener("keydown", function (event) {
      event.stopPropagation();
      if (event.key === "Enter") {
        event.preventDefault();
        input.blur();
      } else if (event.key === "Escape") {
        input.value = original;
        input.blur();
      }
    });
    input.addEventListener("blur", commit);
    return input;
  }
  // One-click owner tagging. Opening the drawer, changing the select, saving
  // and closing it cost five clicks per row; these chips cost one, and post the
  // owner-only endpoint so a tag never rewrites a category, name or remark.
  var OWNER_CHOICES = [["Nic", "Nic"], ["Shared", "Shared"], ["Yx", "Yx"]];
  var OWNER_BATCH_LIMIT = 100;
  function saveOwner(ids, owner, picker, describe) {
    var buttons = picker.querySelectorAll("button");
    var scrollTop = window.scrollY;
    picker.classList.add("owner-picker-saving");
    Array.prototype.forEach.call(buttons, function (button) { button.disabled = true; });
    function release() {
      picker.classList.remove("owner-picker-saving");
      Array.prototype.forEach.call(buttons, function (button) { button.disabled = false; });
    }
    fetch("api/owner", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // A single row keeps posting "id" so the request stays the shape the
      // endpoint has always accepted; only batches need the list form.
      body: JSON.stringify(ids.length === 1
        ? { id: ids[0], owner: owner }
        : { ids: ids, owner: owner })
    }).then(function (response) {
      return response.json().catch(function () {
        return { error: "The local server returned an unreadable response." };
      }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "Owner save failed.");
        return payload;
      });
    }).then(function (payload) {
      if (!applySavedRows(payload)) return refetchAfterSave();
    }).then(function () {
      setTab("transactions");
      // Tagging runs down a list, so the page must not jump back to the top
      // between rows the way a plain re-render would leave it.
      window.scrollTo(0, scrollTop);
      showToast(
        (ids.length === 1 ? describe : ids.length + " transactions") + " -> " +
        (owner === "Untagged" ? "unassigned" : owner) + ".", "success");
    }).catch(function (error) {
      release();
      showToast(error.message, "error");
    });
  }
  function buildOwnerPicker(ids, owner, describe) {
    var picker = el("div", "owner-picker");
    if (!editor.available || !ids.length) {
      picker.appendChild(el("span", "owner-tag", owner === "Untagged" ? "—" : owner));
      if (!editor.available) picker.title = "Start scripts/serve.py to enable editing.";
      return picker;
    }
    picker.setAttribute("role", "group");
    picker.setAttribute("aria-label", "Owner for " + describe);
    OWNER_CHOICES.forEach(function (choice) {
      var active = owner === choice[0];
      var button = el("button", "owner-chip" + (active ? " is-active" : ""), choice[1]);
      button.type = "button";
      button.setAttribute("aria-pressed", active ? "true" : "false");
      button.title = (active ? "Assigned to " + choice[1] + ". Click to unassign."
        : "Assign to " + choice[1] + ".") +
        (ids.length > 1 ? " Applies to all " + ids.length + " transactions." : "");
      button.addEventListener("click", function (event) {
        // The row itself opens the drawer; a chip must not do both.
        event.stopPropagation();
        var next = active ? "Untagged" : choice[0];
        if (ids.length > 1 && !window.confirm(
            "Set " + ids.length + " transactions in \"" + describe + "\" to " +
            (next === "Untagged" ? "unassigned" : next) + "?")) return;
        saveOwner(ids, next, picker, describe);
      });
      picker.appendChild(button);
    });
    return picker;
  }
  function buildOwnerBulkAction(rows) {
    if (!editor.available || !rows.length) return null;
    var ids = rows.slice(0, OWNER_BATCH_LIMIT).map(function (t) { return t.id; });
    var wrap = el("div", "owner-bulk");
    wrap.appendChild(el("span", "owner-bulk-label",
      ids.length < rows.length
        ? "Tag first " + ids.length + " shown as"
        : "Tag all " + ids.length + " shown as"));
    var picker = el("div", "owner-picker");
    OWNER_CHOICES.forEach(function (choice) {
      var button = el("button", "owner-chip", choice[1]);
      button.type = "button";
      button.title = "Assign " + ids.length + " filtered transaction(s) to " + choice[1] + ".";
      button.addEventListener("click", function () {
        if (!window.confirm(
            "Set " + ids.length + " transaction(s) to " + choice[0] + "?")) return;
        saveOwner(ids, choice[0], picker, ids.length + " transactions");
      });
      picker.appendChild(button);
    });
    wrap.appendChild(picker);
    return wrap;
  }
  function saveRiskReview(t, recognized, button, status) {
    button.disabled = true;
    status.textContent = "Saving review...";
    fetch("api/risk-review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ids: t.risk.groupIds,
        recognized: recognized,
        // Decisions are recorded against the signal, not the rows, so a check
        // that has gained a new reason since this page loaded is rejected
        // instead of being acknowledged unseen.
        key: t.risk.key
      })
    }).then(function (response) {
      return response.json().catch(function () {
        return { error: "The local server returned an unreadable response." };
      }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "Review save failed.");
        return payload;
      });
    }).then(function (payload) {
      if (!applySavedRows(payload)) return refetchAfterSave();
    }).then(function () {
      setTab("transactions");
      reopenDrawerFor(t.id);
      showToast(
        recognized
          ? "Transaction marked as recognized."
          : "Transaction returned to review.",
        "success"
      );
    }).catch(function (error) {
      status.textContent = error.message;
      button.disabled = false;
      showToast(error.message, "error");
    });
  }
  function buildRiskEditor(t) {
    if (!t.risk) return null;
    var risk = t.risk;
    var wrap = el("div", "risk-editor risk-" + risk.severity +
      (risk.recognized ? " recognized" : ""));
    var copy = el("div", "risk-editor-copy");
    copy.appendChild(el("strong", "", risk.recognized
      ? "Transaction check resolved"
      : risk.severity.charAt(0).toUpperCase() + risk.severity.slice(1) + " priority check"));
    copy.appendChild(el("span", "", risk.reasons.join(" · ")));
    copy.appendChild(el("small", "",
      "Refunds are netted before this check. This is an anomaly, not a fraud verdict."));
    wrap.appendChild(copy);
    var controls = el("div", "risk-editor-controls");
    var button = el("button", "risk-review-button",
      risk.recognized ? "Review again" : "I recognize this");
    button.disabled = !editor.available;
    var status = el("span", "risk-review-status", editor.available
      ? "" : "Start scripts/serve.py to save this decision.");
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      saveRiskReview(t, !risk.recognized, button, status);
    });
    controls.appendChild(button);
    wrap.appendChild(controls);
    wrap.appendChild(status);
    return wrap;
  }
  function saveAccountReview(t, reviewed, button, status) {
    button.disabled = true;
    status.textContent = "Saving review...";
    fetch("api/account-review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: t.id,
        reviewed: reviewed,
        checksById: (function () {
          var result = {};
          result[t.id] = (t.accountReview && t.accountReview.checks) || [];
          return result;
        }())
      })
    }).then(function (response) {
      return response.json().catch(function () {
        return { error: "The local server returned an unreadable response." };
      }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "Bank review save failed.");
        return payload;
      });
    }).then(function () {
      if (reviewed) {
        accountReviewedSignals[t.id] = (t.accountReview.checks || []).slice();
      } else delete accountReviewedSignals[t.id];
      refreshAccountAnalysis();
      renderLedger();
      var updated = account.transactions.find(function (row) { return row.id === t.id; });
      if (updated && editor.drawerTransactionId === t.id) openTransactionDrawer(updated);
      showToast(reviewed ? "Bank transaction marked as reviewed." :
        "Bank transaction returned to review.", "success");
    }).catch(function (error) {
      status.textContent = error.message;
      button.disabled = false;
      showToast(error.message, "error");
    });
  }
  // One request per chunk rather than one per row: the endpoint accepts a list,
  // and a merchant with dozens of flagged rows used to mean dozens of round
  // trips, each rewriting and revalidating the same file. The server advertises
  // its own cap in /api/status; the default matches the documented limit for a
  // server that has not been restarted yet.
  function saveAccountReviewBatch(ids, button) {
    var total = ids.length;
    if (!total) return;
    button.disabled = true;
    var limit = Math.max(1, Math.floor(Number(editor.accountReviewBatchLimit) ||
      ACCOUNT_REVIEW_BATCH_LIMIT));
    var chunks = [];
    for (var start = 0; start < total; start += limit) {
      chunks.push(ids.slice(start, start + limit));
    }
    var saved = 0;
    var chain = Promise.resolve();
    chunks.forEach(function (chunk) {
      chain = chain.then(function () {
        button.textContent = "Reviewing " +
          Math.min(saved + chunk.length, total) + "/" + total + "...";
        return fetch("api/account-review", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ids: chunk,
            reviewed: true,
            checksById: chunk.reduce(function (result, id) {
              var row = account.transactions.find(function (item) { return item.id === id; });
              result[id] = row && row.accountReview ? row.accountReview.checks : [];
              return result;
            }, {})
          })
        }).then(function (response) {
          return response.json().catch(function () {
            return { error: "The local server returned an unreadable response." };
          }).then(function (payload) {
            if (!response.ok) throw new Error(payload.error || "Bank review save failed.");
            // The response names the ids it actually recorded; trust that over
            // the ids we asked about, so a partially accepted chunk is counted
            // and shown as what it was.
            var applied = Array.isArray(payload.ids) && payload.ids.length
              ? payload.ids : chunk;
            applied.forEach(function (id) {
              if (payload.reviewed === false) delete accountReviewedSignals[id];
              else {
                var row = account.transactions.find(function (item) { return item.id === id; });
                accountReviewedSignals[id] = row && row.accountReview
                  ? row.accountReview.checks.slice() : [];
              }
            });
            saved += applied.length;
          });
        });
      });
    });
    chain.then(function () {
      refreshAccountAnalysis();
      renderLedger();
      showToast(saved + " bank transaction" + (saved === 1 ? "" : "s") +
        " marked as reviewed.", "success");
    }).catch(function (error) {
      // Each request is atomic on the server, so a failure part way through
      // leaves the earlier chunks safely recorded; the re-render shows what is
      // left.
      refreshAccountAnalysis();
      renderLedger();
      showToast(saved + " of " + total + " saved, then: " + error.message, "error");
    });
  }
  function buildAccountReviewEditor(t) {
    var review = t.accountReview || { reasons: [], reviewed: false };
    var wrap = el("div", "risk-editor account-review-editor" +
      (review.reviewed ? " recognized" : ""));
    var copy = el("div", "risk-editor-copy");
    copy.appendChild(el("strong", "", review.reviewed
      ? "Bank transaction reviewed"
      : (review.requiresReview ? "Needs your review" : "Review status")));
    copy.appendChild(el("span", "", review.reasons.length
      ? review.reasons.join(" · ")
      : "No automated concern was found; you can still mark this row as checked."));
    copy.appendChild(el("small", "",
      "Review prompts use statement history only and are not fraud verdicts."));
    wrap.appendChild(copy);
    var controls = el("div", "risk-editor-controls");
    var button = el("button", "risk-review-button",
      review.reviewed ? "Review again" : "Mark reviewed");
    button.disabled = !editor.available;
    var status = el("span", "risk-review-status", editor.available
      ? "" : "Start scripts/serve.py to save this decision.");
    button.addEventListener("click", function (event) {
      event.stopPropagation();
      saveAccountReview(t, !review.reviewed, button, status);
    });
    controls.appendChild(button);
    wrap.appendChild(controls);
    wrap.appendChild(status);
    return wrap;
  }
  function drawerField(labelText, control, helperText) {
    var field = el("label", "drawer-field");
    field.appendChild(el("span", "drawer-field-label", labelText));
    field.appendChild(control);
    if (helperText) field.appendChild(el("small", "", helperText));
    return field;
  }
  function drawerMetaRow(label, value, mono) {
    var row = el("div", "drawer-meta-row");
    row.appendChild(el("span", "", label));
    row.appendChild(el("strong", mono ? "mono" : "", value));
    return row;
  }
  function saveTransactionDetail(t, values, button, status) {
    button.disabled = true;
    status.textContent = "Saving, rebuilding and validating…";
    fetch("api/transaction-detail", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: t.id,
        owner: values.owner,
        category: values.category,
        displayName: values.displayName,
        remark: values.remark
      })
    }).then(function (response) {
      return response.json().catch(function () {
        return { error: "The local server returned an unreadable response." };
      }).then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "Transaction save failed.");
        return payload;
      });
    }).then(function (payload) {
      if (!applySavedRows(payload)) return refetchAfterSave();
    }).then(function () {
      reopenDrawerFor(t.id);
      showToast("Transaction details saved and validated.", "success");
    }).catch(function (error) {
      status.textContent = error.message;
      button.disabled = false;
      showToast(error.message, "error");
    });
  }
  function renderTransactionDrawer(t) {
    var title = document.getElementById("transaction-drawer-title");
    var eyebrow = document.getElementById("transaction-drawer-eyebrow");
    var body = document.getElementById("transaction-drawer-body");
    clear(body);
    title.textContent = transactionName(t);
    eyebrow.textContent = dateLabel(t.date || (t.month + "-01")) + " · " +
      (t.type === "debit" ? "Purchase" : "Credit");

    var summary = el("section", "drawer-summary");
    var amount = el("strong", "drawer-amount " + (t.type === "debit" ? "" : "credit"),
      (t.type === "debit" ? "−" : "+") + fmt(t.amount));
    summary.appendChild(amount);
    var summaryTags = el("div", "drawer-summary-tags");
    summaryTags.appendChild(el("span", "cat-pill " + catClass(t.category), t.category));
    if (t.foodpanda) {
      summaryTags.appendChild(el("span", "source-badge foodpanda-source", "Foodpanda"));
    }
    if (t.shopee) {
      summaryTags.appendChild(el("span", "source-badge shopee-source", "Shopee"));
    }
    if (t.grab) {
      summaryTags.appendChild(el("span", "source-badge grab-source", "Grab"));
      if (t.grab.status === "unreconciled") {
        summaryTags.appendChild(el("span", "source-badge unreconciled-source", "Unreconciled funding"));
      }
      if (t.grab.corporate) {
        summaryTags.appendChild(el("span", "source-badge corporate-source", "Corporate · excluded"));
      }
    }
    summaryTags.appendChild(el("span", "owner-tag drawer-owner",
      t.owner === "Untagged" ? "Unassigned" : t.owner));
    if (t.provenance && t.provenance.verified) {
      summaryTags.appendChild(el("span", "drawer-verified", "Source verified"));
    }
    summary.appendChild(summaryTags);
    body.appendChild(summary);

    if (t.foodpanda) {
      var orderEvidence = el("section", "drawer-section foodpanda-evidence");
      orderEvidence.appendChild(el("h3", "", "Foodpanda order"));
      var orderMeta = el("div", "drawer-meta");
      orderMeta.appendChild(drawerMetaRow("Merchant", t.foodpanda.merchant));
      orderMeta.appendChild(drawerMetaRow("Order number", t.foodpanda.orderId, true));
      orderMeta.appendChild(drawerMetaRow(
        "Ordered", dateLabel(t.foodpanda.date) + " at " + t.foodpanda.time));
      orderMeta.appendChild(drawerMetaRow(
        "Fulfilment", t.foodpanda.fulfillment === "pickup" ? "Pickup" : "Delivery"));
      orderMeta.appendChild(drawerMetaRow("Order total", fmt(t.foodpanda.amount)));
      orderEvidence.appendChild(orderMeta);
      body.appendChild(orderEvidence);
    }
    if (t.shopee) {
      var shopeeEvidence = el("section", "drawer-section shopee-evidence");
      shopeeEvidence.appendChild(el("h3", "", "Shopee order"));
      var shopeeMeta = el("div", "drawer-meta");
      shopeeMeta.appendChild(drawerMetaRow("Seller", t.shopee.merchant));
      shopeeMeta.appendChild(drawerMetaRow("Order number", t.shopee.orderId, true));
      shopeeMeta.appendChild(drawerMetaRow("Status",
        t.shopee.status.replace(/-/g, " ").replace(/^./, function (c) { return c.toUpperCase(); })));
      shopeeMeta.appendChild(drawerMetaRow("Order total", fmt(t.shopee.amount)));
      shopeeEvidence.appendChild(shopeeMeta);
      if (Array.isArray(t.shopee.items) && t.shopee.items.length) {
        shopeeEvidence.appendChild(el("h4", "drawer-subheading", "Items"));
        var itemList = el("ul", "drawer-item-list");
        t.shopee.items.forEach(function (item) {
          itemList.appendChild(el("li", "", item));
        });
        shopeeEvidence.appendChild(itemList);
      }
      body.appendChild(shopeeEvidence);
    }
    if (t.grab && Array.isArray(t.grab.receipts)) {
      t.grab.receipts.forEach(function (receipt, index) {
        var grabEvidence = el("section", "drawer-section grab-evidence");
        grabEvidence.appendChild(el("h3", "",
          t.grab.receipts.length > 1 ? "Grab receipt " + (index + 1) : "Grab receipt"));
        var grabMeta = el("div", "drawer-meta");
        grabMeta.appendChild(drawerMetaRow("Service", receipt.service));
        if (receipt.merchant && receipt.merchant !== "Grab") {
          grabMeta.appendChild(drawerMetaRow("Merchant", receipt.merchant));
        }
        grabMeta.appendChild(drawerMetaRow("Receipt", receipt.receiptId, true));
        grabMeta.appendChild(drawerMetaRow("Receipt total",
          (receipt.currency === "MYR" ? "RM " : "S$") +
          receipt.amount.toLocaleString("en-SG", { minimumFractionDigits: 2, maximumFractionDigits: 2 })));
        grabMeta.appendChild(drawerMetaRow("Profile",
          receipt.profile.replace(/^./, function (c) { return c.toUpperCase(); }) +
          (receipt.corporate ? " · excluded from personal finance" : "")));
        if (receipt.paymentMethod) {
          grabMeta.appendChild(drawerMetaRow("Paid by", receipt.paymentMethod));
        }
        if (Array.isArray(receipt.evidenceSources) && receipt.evidenceSources.length) {
          grabMeta.appendChild(drawerMetaRow("Source", receipt.evidenceSources.join(" + ")));
        }
        if (receipt.webHistoryAmountDiffers) {
          grabMeta.appendChild(drawerMetaRow("Grab history total",
            (receipt.currency === "MYR" ? "RM " : "S$") +
            receipt.webHistoryAmount.toLocaleString("en-SG", {
              minimumFractionDigits: 2, maximumFractionDigits: 2
            }) + " · differs from email receipt"));
        }
        if (receipt.category !== "Food & dining" && receipt.category !== "Groceries" &&
            receipt.pickup && receipt.dropoff) {
          grabMeta.appendChild(drawerMetaRow("Route",
            (receipt.pickupLabel || receipt.pickup) + " → " +
            (receipt.dropoffLabel || receipt.dropoff)));
        }
        grabEvidence.appendChild(grabMeta);
        if (receipt.category !== "Food & dining" && receipt.category !== "Groceries" &&
            Array.isArray(receipt.items) && receipt.items.length) {
          grabEvidence.appendChild(el("h4", "drawer-subheading", "Items"));
          var grabItems = el("ul", "drawer-item-list");
          receipt.items.forEach(function (item) { grabItems.appendChild(el("li", "", item)); });
          grabEvidence.appendChild(grabItems);
        }
        body.appendChild(grabEvidence);
      });
    }
    if (t.grab && t.grab.status === "unreconciled") {
      var grabUnreconciled = el("section", "drawer-section grab-unreconciled-evidence");
      grabUnreconciled.appendChild(el("h3", "", "Grab statement charge"));
      var grabUnreconciledMeta = el("div", "drawer-meta");
      grabUnreconciledMeta.appendChild(drawerMetaRow("Status", "Wallet funding · unreconciled"));
      grabUnreconciledMeta.appendChild(drawerMetaRow("Receipt", "No receipt safely linked"));
      grabUnreconciledMeta.appendChild(drawerMetaRow(
        "Treatment", t.categorySource === "manual-override"
          ? "Included in card outflow; using your saved category override"
          : "Included in card outflow; not attributed to food or transport"));
      grabUnreconciled.appendChild(grabUnreconciledMeta);
      body.appendChild(grabUnreconciled);
    }

    if (t.risk) {
      var riskEditor = buildRiskEditor(t);
      if (riskEditor) body.appendChild(riskEditor);
    }

    var editSection = el("section", "drawer-section");
    editSection.appendChild(el("h3", "", "Edit transaction"));
    var form = el("form", "drawer-form");
    var displayName = document.createElement("input");
    displayName.type = "text";
    displayName.maxLength = 100;
    displayName.value = t.displayName || "";
    displayName.placeholder = t.description;
    displayName.disabled = !editor.available;
    form.appendChild(drawerField(
      "Display name",
      displayName,
      "A clearer label for the dashboard. The statement description stays unchanged."
    ));

    var category = document.createElement("select");
    CATEGORY_ORDER.forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name;
      category.appendChild(option);
    });
    category.value = t.category;
    category.disabled = !editor.available;
    form.appendChild(drawerField(
      "Category",
      category,
      (t.ruleCategories || []).length > 1
        ? "Rules suggest " + t.ruleCategories.join(" or ") +
          ". Saving confirms the selected category."
        : t.categorySource === "foodpanda-order"
        ? "Confirmed from the matched Foodpanda merchant."
        : t.categorySource === "grab-receipt"
        ? "Confirmed from the matched personal Grab receipt."
        : t.categorySource === "grab-corporate"
        ? "Excluded because the matched Grab receipt uses a corporate profile."
        : t.categorySource === "grab-unreconciled"
        ? "No receipt could be safely linked; kept as unallocated Grab wallet funding."
        : t.category === t.ruleCategory
        ? "Currently assigned by the merchant rule."
        : "Manual override. Select " + t.ruleCategory + " to restore the merchant rule."
    ));

    var owner = document.createElement("select");
    [
      ["Nic", "Nic"],
      ["Shared", "Shared"],
      ["Yx", "Yx"],
      ["Untagged", "Unassigned"]
    ].forEach(function (item) {
      var option = document.createElement("option");
      option.value = item[0];
      option.textContent = item[1];
      owner.appendChild(option);
    });
    owner.value = t.owner;
    owner.disabled = !editor.available;
    form.appendChild(drawerField(
      "Owner",
      owner,
      "Current source: " + ownerSourceLabel(t.ownerSource) + "."
    ));

    var remark = document.createElement("textarea");
    remark.maxLength = 240;
    remark.rows = 3;
    remark.value = t.remark || "";
    remark.placeholder = "What was this purchase for?";
    remark.disabled = !editor.available;
    form.appendChild(drawerField(
      "Remarks",
      remark,
      "Private context that remains attached to this exact transaction."
    ));

    var actions = el("div", "drawer-form-actions");
    var status = el("span", "drawer-save-status", editor.available
      ? "Changes are saved by stable transaction ID."
      : "Start scripts/serve.py to enable editing.");
    var save = el("button", "drawer-save", "Save details");
    save.type = "submit";
    save.disabled = !editor.available;
    actions.appendChild(status);
    actions.appendChild(save);
    form.appendChild(actions);
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      saveTransactionDetail(t, {
        owner: owner.value,
        category: category.value,
        displayName: displayName.value.trim().replace(/\s+/g, " "),
        remark: remark.value.trim().replace(/\s+/g, " ")
      }, save, status);
    });
    editSection.appendChild(form);
    body.appendChild(editSection);

    var evidence = el("section", "drawer-section");
    evidence.appendChild(el("h3", "", "Statement evidence"));
    var meta = el("div", "drawer-meta");
    meta.appendChild(drawerMetaRow("Original description", t.description));
    meta.appendChild(drawerMetaRow("Transaction date", t.date || "Not available"));
    meta.appendChild(drawerMetaRow("Posted date", t.postedDate || t.date || "Not available"));
    meta.appendChild(drawerMetaRow("Statement", statementLabel(t.month)));
    meta.appendChild(drawerMetaRow("Card", t.card || "UOB ONE CARD"));
    meta.appendChild(drawerMetaRow("Type", t.type));
    if (t.foreign) meta.appendChild(drawerMetaRow("Foreign amount", t.foreign));
    meta.appendChild(drawerMetaRow(
      "Source",
      t.provenance
        ? t.provenance.sourceFile +
          (t.provenance.page ? ", page " + t.provenance.page : "")
        : "Legacy build"
    ));
    meta.appendChild(drawerMetaRow(
      "Source status",
      t.provenance && t.provenance.verified ? "Verified" : "Needs review"
    ));
    meta.appendChild(drawerMetaRow("Transaction ID", t.id, true));
    evidence.appendChild(meta);
    body.appendChild(evidence);
  }
  function renderAccountTransactionDrawer(t) {
    var title = document.getElementById("transaction-drawer-title");
    var eyebrow = document.getElementById("transaction-drawer-eyebrow");
    var body = document.getElementById("transaction-drawer-body");
    clear(body);
    title.textContent = t.counterparty ||
      window.FinanceGrouping.accountCounterparty(t.description);
    eyebrow.textContent = dateLabel(t.date || (t.month + "-01")) + " · Bank account";

    var deposit = t.direction === "deposit";
    var summary = el("section", "drawer-summary");
    summary.appendChild(el("strong", "drawer-amount " + (deposit ? "credit" : ""),
      (deposit ? "+" : "−") + fmt(t.amount)));
    var tags = el("div", "drawer-summary-tags");
    tags.appendChild(el("span", "cat-pill bank-flow", t.flow));
    tags.appendChild(el("span", "bank-direction " + (deposit ? "money-in" : "money-out"),
      deposit ? "Money in" : "Money out"));
    if (t.provenance && t.provenance.verified) {
      tags.appendChild(el("span", "drawer-verified", "Source verified"));
    }
    summary.appendChild(tags);
    body.appendChild(summary);

    body.appendChild(buildAccountReviewEditor(t));

    var evidence = el("section", "drawer-section");
    evidence.appendChild(el("h3", "", "Account statement evidence"));
    var meta = el("div", "drawer-meta");
    meta.appendChild(drawerMetaRow("Original description", t.description));
    meta.appendChild(drawerMetaRow("Transaction date", t.date || "Not available"));
    meta.appendChild(drawerMetaRow("Statement", statementLabel(t.month)));
    meta.appendChild(drawerMetaRow("Direction", deposit ? "Deposit" : "Withdrawal"));
    meta.appendChild(drawerMetaRow("Flow", t.flow || "Other"));
    meta.appendChild(drawerMetaRow("Counterparty", t.counterparty ||
      window.FinanceGrouping.accountCounterparty(t.description)));
    meta.appendChild(drawerMetaRow("Balance after transaction", fmt(t.balance)));
    meta.appendChild(drawerMetaRow("Amount source", t.amountSource || "Not recorded"));
    meta.appendChild(drawerMetaRow(
      "Source",
      t.provenance
        ? t.provenance.sourceFile + (t.provenance.page ? ", page " + t.provenance.page : "")
        : "Legacy build"
    ));
    meta.appendChild(drawerMetaRow(
      "Source status",
      t.provenance && t.provenance.verified ? "Verified" : "Needs review"
    ));
    meta.appendChild(drawerMetaRow("Transaction ID", t.id, true));
    evidence.appendChild(meta);
    body.appendChild(evidence);
  }
  function openTransactionDrawer(t) {
    closeAuditHistory();
    var shell = document.getElementById("transaction-drawer-shell");
    if (editor.drawerCloseTimer) window.clearTimeout(editor.drawerCloseTimer);
    if (!editor.drawerTransactionId) editor.drawerLastFocus = document.activeElement;
    editor.drawerTransactionId = t.id;
    if (t.direction) renderAccountTransactionDrawer(t);
    else renderTransactionDrawer(t);
    shell.classList.remove("hidden");
    shell.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
    window.requestAnimationFrame(function () {
      shell.classList.add("is-open");
    });
    document.getElementById("transaction-drawer-close").focus();
  }
  function closeTransactionDrawer() {
    var shell = document.getElementById("transaction-drawer-shell");
    if (!editor.drawerTransactionId) return;
    shell.classList.remove("is-open");
    shell.setAttribute("aria-hidden", "true");
    document.body.classList.remove("drawer-open");
    editor.drawerTransactionId = null;
    editor.drawerCloseTimer = window.setTimeout(function () {
      shell.classList.add("hidden");
    }, reducedMotion() ? 0 : 220);
    if (editor.drawerLastFocus && document.body.contains(editor.drawerLastFocus)) {
      editor.drawerLastFocus.focus();
    }
    editor.drawerLastFocus = null;
  }
  function auditValue(value) {
    if (value === undefined || value === null || value === "") return "Empty";
    if (value === "Untagged") return "Unassigned";
    return String(value);
  }
  function auditTime(timestamp) {
    var date = new Date(timestamp);
    if (isNaN(date.getTime())) return timestamp || "Unknown time";
    return date.toLocaleString("en-SG", {
      day: "numeric",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit"
    });
  }
  function renderAuditHistory(entries, total) {
    var list = document.getElementById("audit-history-list");
    clear(list);
    if (!entries.length) {
      list.appendChild(el(
        "div",
        "audit-empty",
        "No recorded changes yet. The next transaction edit will appear here."
      ));
      return;
    }
    // The server caps its response at the most recent 250 entries but reports
    // the true count. Without this the panel silently looks like the whole
    // history.
    var recorded = Number(total);
    if (recorded > entries.length) {
      list.appendChild(el("div", "audit-truncated",
        "Showing last " + entries.length + " of " + recorded + " edits."));
    }
    entries.forEach(function (entry) {
      var item = el("article", "audit-entry");
      var head = el("div", "audit-entry-head");
      var title = el("div", "audit-entry-title");
      title.appendChild(el("strong", "", entry.description || "Transaction"));
      title.appendChild(el("span", "", entry.action || "Updated transaction"));
      head.appendChild(title);
      head.appendChild(el("time", "audit-entry-time", auditTime(entry.timestamp)));
      item.appendChild(head);
      var changes = el("div", "audit-changes");
      (entry.changes || []).forEach(function (change) {
        var row = el("div", "audit-change");
        row.appendChild(el("span", "", change.field));
        var values = el("span", "audit-change-values");
        values.appendChild(el("span", "audit-before", auditValue(change.before)));
        values.appendChild(el("span", "audit-arrow", "→"));
        values.appendChild(el("span", "audit-after", auditValue(change.after)));
        row.appendChild(values);
        changes.appendChild(row);
      });
      item.appendChild(changes);
      if (entry.transactionId) {
        item.tabIndex = 0;
        item.setAttribute("role", "button");
        item.title = "Open this transaction";
        function openEntry() {
          var transaction = data.transactions.find(function (row) {
            return row.id === entry.transactionId;
          });
          if (!transaction) {
            transaction = account.transactions.find(function (row) {
              return row.id === entry.transactionId;
            });
          }
          if (transaction) openTransactionDrawer(transaction);
        }
        item.addEventListener("click", openEntry);
        item.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            openEntry();
          }
        });
      }
      list.appendChild(item);
    });
  }
  function openAuditHistory() {
    var shell = document.getElementById("audit-history-shell");
    var trigger = document.activeElement;
    closeTransactionDrawer();
    if (editor.auditCloseTimer) window.clearTimeout(editor.auditCloseTimer);
    editor.auditOpen = true;
    editor.auditLastFocus = trigger;
    shell.classList.remove("hidden");
    shell.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
    var list = document.getElementById("audit-history-list");
    clear(list);
    list.appendChild(el("div", "audit-loading", "Loading change history…"));
    window.requestAnimationFrame(function () { shell.classList.add("is-open"); });
    document.getElementById("audit-history-close").focus();
    loadJson("api/audit-history?updated=" + Date.now()).then(function (history) {
      renderAuditHistory(history.entries || [], history.total);
    }).catch(function (error) {
      clear(list);
      list.appendChild(el("div", "audit-empty", "Change history could not be loaded."));
      showToast(error.message, "error");
    });
  }
  function closeAuditHistory() {
    if (!editor.auditOpen) return;
    var shell = document.getElementById("audit-history-shell");
    shell.classList.remove("is-open");
    shell.setAttribute("aria-hidden", "true");
    document.body.classList.remove("drawer-open");
    editor.auditOpen = false;
    editor.auditCloseTimer = window.setTimeout(function () {
      shell.classList.add("hidden");
    }, reducedMotion() ? 0 : 220);
    if (editor.auditLastFocus && document.body.contains(editor.auditLastFocus)) {
      editor.auditLastFocus.focus();
    }
    editor.auditLastFocus = null;
  }
  function appendExpandableRow(body, tr, t, colSpan, details, namespace) {
    tr.classList.add("tx-row");
    tr.tabIndex = 0;
    tr.setAttribute("aria-haspopup", "dialog");
    // A focusable <tr> announces nothing on its own; the label names the
    // action and the row so the focus stop is meaningful. role="button" is
    // deliberately not used because it would strip the row/cell semantics.
    tr.setAttribute("aria-label", "Open transaction details for " +
      (t.displayName || t.description || t.counterparty || "transaction"));
    tr.title = "Open transaction details";
    tr.addEventListener("click", function () { openTransactionDrawer(t); });
    tr.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openTransactionDrawer(t);
      }
    });
    body.appendChild(tr);
  }

  function cardTx(month) {
    return data.transactions.filter(function (t) { return t.month === month && !EXCLUDED[t.category]; });
  }
  function cardSpend(month) {
    var total = 0;
    cardTx(month).forEach(function (t) { total += signed(t); });
    return total;
  }
  function accountFor(month) {
    return account.transactions.filter(function (t) { return t.month === month; });
  }
  function incomeFor(month) {
    var total = 0;
    accountFor(month).forEach(function (t) {
      if (t.direction === "deposit" && t.flow === "Salary") total += t.amount;
    });
    return total;
  }
  function investedFor(month) {
    var total = 0;
    accountFor(month).forEach(function (t) {
      if (t.direction === "withdrawal" && WEALTH[t.flow]) total += t.amount;
    });
    return total;
  }
  function hasAccount(month) { return account.months.indexOf(month) !== -1; }

  function icon(name, cls) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  function emptyState(title, detail, iconName) {
    var stateNode = el("div", "empty-state");
    var badge = el("span", "empty-state-icon");
    badge.appendChild(icon(iconName || "receipt"));
    stateNode.appendChild(badge);
    var copy = el("div", "");
    copy.appendChild(el("strong", "", title));
    if (detail) copy.appendChild(el("p", "", detail));
    stateNode.appendChild(copy);
    return stateNode;
  }

  function metric(label, value, note, tone, iconName) {
    var card = el("div", "kpi");
    var head = el("p", "label");
    if (iconName) head.appendChild(icon(iconName));
    head.appendChild(document.createTextNode(label));
    card.appendChild(head);
    var valueNode = el("p", "value" + (tone ? " " + tone : ""), value);
    card.appendChild(valueNode);
    if (note) card.appendChild(el("p", "delta", note));
    window.requestAnimationFrame(function () { animateNumber(valueNode); });
    return card;
  }

  function syncOwnerPills() {
    Array.prototype.forEach.call(document.getElementById("owner-pills").children, function (button) {
      var active = button.textContent === state.owner;
      button.classList.toggle("active", active);
      setPressed(button, active);
    });
  }

  function populateTransactionCategoryFilter() {
    var select = document.getElementById("category-filter");
    clear(select);
    var values = {};
    if (state.transactionSource === "bank") {
      account.transactions.forEach(function (t) { values[t.flow || "Other"] = true; });
    } else {
      data.transactions.forEach(function (t) { values[t.category] = true; });
    }
    var options = ["All"].concat(Object.keys(values).sort());
    // Recategorizing the last row of a category retires that category. Keep the
    // current selection when it still exists, otherwise fall back to "All" so
    // the control never points at a filter that can match nothing.
    if (options.indexOf(state.category) === -1) state.category = "All";
    options.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = value === "All"
        ? (state.transactionSource === "bank" ? "All flows" : "All categories")
        : value;
      select.appendChild(option);
    });
    select.value = state.category;
    select.setAttribute("aria-label", state.transactionSource === "bank"
      ? "Filter by bank flow" : "Filter by category");
  }

  // The bank rules describe configuration, so they are written from it rather
  // than spelled out in the markup.
  function renderBankRules() {
    var identity = window.FinanceGrouping.getIdentity();
    var names = identity.trustedCounterparties;
    var list = document.getElementById("trusted-counterparties-list");
    if (list) {
      list.textContent = names.length
        ? names.slice(0, -1).join(", ") +
          (names.length > 1 ? " and " : "") + names[names.length - 1]
        : "none configured";
    }
    var accounts = document.getElementById("known-accounts-count");
    if (accounts) {
      var count = Object.keys(identity.knownAccounts).length;
      accounts.textContent = count
        ? count + (count === 1 ? " account is" : " accounts are") + " configured by name."
        : "No accounts are configured by name.";
    }
  }

  function syncTransactionSourceControls() {
    var bank = state.transactionSource === "bank";
    renderBankRules();
    Array.prototype.forEach.call(
      document.getElementById("transaction-source-pills").children,
      function (button) {
        var active = button.getAttribute("data-source") === state.transactionSource;
        button.classList.toggle("active", active);
        setPressed(button, active);
      }
    );
    document.getElementById("owner-pills").classList.toggle("hidden", bank);
    document.getElementById("show-excluded-label").classList.toggle("hidden", bank);
    var foodpandaFilter = document.getElementById("foodpanda-filter");
    foodpandaFilter.classList.toggle("hidden", bank);
    foodpandaFilter.classList.toggle("active", state.foodpandaOnly);
    setPressed(foodpandaFilter, state.foodpandaOnly);
    var shopeeFilter = document.getElementById("shopee-filter");
    shopeeFilter.classList.toggle("hidden", bank);
    shopeeFilter.classList.toggle("active", state.shopeeOnly);
    setPressed(shopeeFilter, state.shopeeOnly);
    var grabFilter = document.getElementById("grab-filter");
    grabFilter.classList.toggle("hidden", bank);
    grabFilter.classList.toggle("active", state.grabOnly);
    setPressed(grabFilter, state.grabOnly);
    document.getElementById("bank-review-controls").classList.toggle("hidden", !bank);
    document.getElementById("card-rules").classList.toggle("hidden", bank);
    document.getElementById("bank-rules").classList.toggle("hidden", !bank);
    document.getElementById("group-purchases-label").textContent = state.groupPurchases
      ? "Show individual"
      : (bank ? "Group counterparties" : "Group purchases");
  }

  function setTransactionSource(source) {
    if (source === state.transactionSource) return;
    state.transactionSource = source;
    state.category = "All";
    state.reviewMode = null;
    // Card and bank rows have separate id spaces, so an insight drill-down
    // cannot survive the switch.
    setIdFilter(null);
    state.ledgerLimit = LEDGER_CAP;
    var months = transactionMonths();
    if (state.period.mode === "month") {
      var current = state.period.year + "-" + state.period.month;
      if (months.indexOf(current) === -1 && months.length) {
        var earlier = months.filter(function (month) { return month <= current; });
        var fallback = earlier.length ? earlier[earlier.length - 1] : months[months.length - 1];
        state.period.year = fallback.slice(0, 4);
        state.period.month = fallback.slice(5);
      }
    }
    populateTransactionCategoryFilter();
    syncTransactionSourceControls();
    renderPeriod();
    renderLedger();
  }

  function makeActionable(node, label, handler) {
    node.classList.add("actionable");
    node.tabIndex = 0;
    node.setAttribute("role", "button");
    node.setAttribute("aria-label", label);
    node.title = label;
    node.addEventListener("click", handler);
    node.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        handler();
      }
    });
    return node;
  }

  function openTransactions(options) {
    options = options || {};
    state.transactionSource = options.source || "card";
    var month = options.month || state.month;
    if (month && data.months.indexOf(month) !== -1) {
      if (month !== state.month) setMonth(month);
      else document.getElementById("month-select").value = month;
    }
    state.owner = options.owner || "All";
    state.category = options.category || "All";
    state.search = options.search || "";
    state.foodpandaOnly = !!options.foodpandaOnly;
    state.shopeeOnly = !!options.shopeeOnly;
    state.grabOnly = !!options.grabOnly;
    state.reviewMode = options.reviewMode || null;
    state.showExcluded = false;
    // Bank-only filters reset with everything else; a drill-down that landed
    // on the bank view used to inherit a leftover "needs review" or direction
    // filter and silently hide most of the month.
    state.bankReview = "all";
    state.bankDirection = "all";
    state.bankExcludeInternal = false;
    var bankReviewFilter = document.getElementById("bank-review-filter");
    if (bankReviewFilter) bankReviewFilter.value = "all";
    var bankExcludeBox = document.getElementById("bank-exclude-internal");
    if (bankExcludeBox) bankExcludeBox.checked = false;
    var bankPills = document.getElementById("bank-direction-pills");
    if (bankPills) {
      Array.prototype.forEach.call(bankPills.children, function (button) {
        var active = button.textContent === "All";
        button.classList.toggle("active", active);
        setPressed(button, active);
      });
    }
    setIdFilter(options.ids, options.filterLabel);
    // Grouping is a view mode like any other filter here: a drill-down that
    // resets owner, category, search and review must reset it too, or the
    // rows the caller asked for arrive collapsed into merchant groups.
    state.groupPurchases = false;
    if (groupToggle) groupToggle.set(false);
    state.period = options.period || {
      mode: "month",
      year: month.slice(0, 4),
      month: month.slice(5)
    };
    state.ledgerLimit = LEDGER_CAP;
    syncOwnerPills();
    populateTransactionCategoryFilter();
    syncTransactionSourceControls();
    document.getElementById("search").value = state.search;
    document.getElementById("show-excluded").checked = false;
    renderPeriod();
    renderLedger();
    setTab("transactions");
  }

  function setIdFilter(ids, label) {
    if (!ids || !ids.length) {
      state.idFilter = null;
      state.idFilterLabel = "";
      state.idFilterCount = 0;
    } else {
      var map = {};
      ids.forEach(function (id) { map[id] = true; });
      state.idFilter = map;
      state.idFilterLabel = label || "Selected transactions";
      state.idFilterCount = Object.keys(map).length;
    }
    syncIdFilterChip();
  }

  function syncIdFilterChip() {
    var chip = document.getElementById("insight-filter-chip");
    if (!chip) return;
    var active = Boolean(state.idFilter);
    chip.classList.toggle("hidden", !active);
    if (!active) return;
    chip.textContent = state.idFilterLabel + " · " + state.idFilterCount + " ✕";
    chip.title = "Showing the exact transactions behind this insight. " +
      "Click to clear.";
  }

  function openReview(options) {
    options = options || {};
    options.period = { mode: "all", year: state.period.year, month: state.period.month };
    openTransactions(options);
  }

  function qualityCard(title, count, amount, detail, action, options) {
    var card = el("div", "quality-card" + (count ? " needs-review" : " clear"));
    var top = el("div", "quality-card-top");
    top.appendChild(el("span", "quality-card-title", title));
    var countNode = el("span", "quality-count", count ? String(count) : "Clear");
    top.appendChild(countNode);
    card.appendChild(top);
    if (count && amount) {
      var amountNode = el("strong", "quality-amount", fmt(amount));
      card.appendChild(amountNode);
      window.requestAnimationFrame(function () { animateNumber(amountNode); });
    }
    window.requestAnimationFrame(function () { animateCount(countNode); });
    card.appendChild(el("p", "quality-detail", detail));
    if (count && action) {
      var button = el("button", "quality-action", action);
      button.addEventListener("click", function () { openReview(options || {}); });
      card.appendChild(button);
    }
    return card;
  }

  function renderFreshness() {
    var wrap = document.getElementById("statement-freshness");
    clear(wrap);
    var freshness = data.freshness || {};
    var latest = freshness.latestStatementMonth;
    if (!latest) {
      wrap.className = "freshness-strip warn";
      wrap.appendChild(icon("calendar"));
      wrap.appendChild(el("strong", "", "No card statements loaded"));
      wrap.appendChild(el("span", "freshness-detail", "Import a statement to establish coverage."));
      return;
    }

    var missing = freshness.missingStatementMonths || [];
    var expectedDate = freshness.expectedNextStatementDate
      ? localDate(freshness.expectedNextStatementDate) : null;
    var today = new Date();
    today = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    var overdueDays = expectedDate ? dayDifference(today, expectedDate) : 0;
    var generatedDate = data.generatedAt ? localDate(data.generatedAt.replace(" ", "T")) : today;
    var buildAge = Math.max(0, dayDifference(today, generatedDate));
    var tone = missing.length || overdueDays > 5 || buildAge > 7 ? "warn" : "current";
    wrap.className = "freshness-strip " + tone;
    wrap.appendChild(icon(tone === "current" ? "calendar" : "receipt"));

    var copy = el("div", "freshness-copy");
    var title = "Statements current through " + monthLabel(latest);
    if (missing.length) {
      title = missing.length + " missing statement month" + (missing.length === 1 ? "" : "s");
    } else if (overdueDays > 5) {
      title = monthLabel(freshness.expectedNextStatementMonth) + " statement may be missing";
    } else if (buildAge > 7) {
      title = "Dashboard refresh is " + buildAge + " days old";
    }
    copy.appendChild(el("strong", "", title));

    var nextCopy = freshness.expectedNextStatementMonth
      ? "Next: " + monthLabel(freshness.expectedNextStatementMonth) +
        (freshness.expectedNextStatementDate
          ? " around " + dateLabel(freshness.expectedNextStatementDate) : "")
      : "";
    var coverageCopy = "Transactions through " + dateLabel(freshness.sourceThrough);
    copy.appendChild(el("span", "freshness-detail",
      coverageCopy + (nextCopy ? " · " + nextCopy : "")));
    wrap.appendChild(copy);

    var meta = freshness.statementCount + " PDF statement" +
      (freshness.statementCount === 1 ? "" : "s") + " · " +
      (missing.length ? missing.map(monthLabel).join(", ") + " missing" : "no gaps") +
      " · refreshed " + (buildAge === 0 ? "today" : buildAge + "d ago");
    wrap.appendChild(el("span", "freshness-meta", meta));
  }

  function renderDataQuality() {
    var wrap = document.getElementById("data-quality");
    var status = document.getElementById("quality-status");
    clear(wrap);
    var quality = data.quality || {};
    var integrity = quality.integrity || {};
    var provenance = quality.provenance || {};
    var review = quality.review || {};
    var sound = integrity.uniqueIds !== false && !integrity.duplicateIds &&
      !integrity.missingProvenance &&
      integrity.sourceTransactions === integrity.outputTransactions;
    status.className = "quality-status " + (sound ? "pass" : "fail");
    status.textContent = sound ? "Source checks passed" : "Integrity issue";

    var intro = el("div", "quality-intro");
    var introCopy = el("div", "");
    introCopy.appendChild(el("strong", "", sound
      ? "Source checks passed"
      : "A source-to-output integrity check needs attention."));
    introCopy.appendChild(el("p", "", sound
      ? integrity.outputTransactions + " traceable rows · source and count reconciliation passed"
      : "Run python scripts/validate_data.py before trusting or deploying this build."));
    intro.appendChild(introCopy);
    var sourceCounts = provenance.sourceCounts || {};
    var sources = el("div", "source-badges");
    [
      ["PDF", sourceCounts["card-pdf"] || 0],
      ["CSV", sourceCounts["card-csv"] || 0],
      ["Manual", sourceCounts["legacy-manual"] || 0]
    ].forEach(function (item) {
      sources.appendChild(el("span", "source-badge", item[0] + " " + item[1]));
    });
    intro.appendChild(sources);
    wrap.appendChild(intro);

    // Only unresolved items appear. A card that is clear is left out entirely,
    // and when nothing is left the panel hides itself rather than reporting
    // "all clear" at you every visit.
    var grid = el("div", "quality-grid");
    var untagged = review.untagged || {};
    var other = review.otherCategory || {};
    var overlap = review.categoryRuleOverlap || {};
    var lady = review.ladyRuleOrUnassigned || {};
    var split = review.splitByRule || {};
    var suspicious = review.suspicious || {};
    var unverified = provenance.unverifiedTransactions || 0;
    var unverifiedMonths = provenance.unverifiedMonths || [];
    var shown = 0;

    function add(count, card) {
      if (!count) return;
      grid.appendChild(card);
      shown += 1;
    }

    add(suspicious.count, qualityCard(
      "Suspicious transaction checks", suspicious.count || 0, suspicious.amount || 0,
      (suspicious.high || 0) + " high · " +
      (suspicious.medium || 0) + " medium · " +
      (suspicious.low || 0) + " low priority. Refunds have already been netted.",
      "Review transactions", { reviewMode: "suspicious" }
    ));
    add(split.count, qualityCard(
      "Changes what Yx owes", split.count || 0, split.amount || 0,
      "Tagged Shared or Yx by merchant rule, not by you, moving " +
      fmt0(Math.abs(split.owedImpact || 0)) + " of the settlement.",
      "Review these", { reviewMode: "split-by-rule" }
    ));
    add(untagged.count, qualityCard(
      "Owner unassigned", untagged.count || 0, untagged.amount || 0,
      "Excluded from shared-expense settlement until reviewed.",
      "Review owners", { owner: "Untagged" }
    ));
    add(lady.count, qualityCard(
      "Lady card needs confirmation", lady.count || 0, lady.amount || 0,
      "Owner came from an unconfirmed merchant rule or remains unassigned.",
      "Review Lady card", { reviewMode: "lady-unconfirmed" }
    ));
    add(other.count, qualityCard(
      "Category is Other", other.count || 0, other.amount || 0,
      "These merchants are visible but not yet classified.",
      "Review categories", { category: "Other" }
    ));
    add(overlap.count, qualityCard(
      "Category rules overlap", overlap.count || 0, overlap.amount || 0,
      "More than one rule matches these rows; the first rule currently wins.",
      "Review overlaps", { reviewMode: "category-overlap" }
    ));
    add(unverified, qualityCard(
      "Unverified source rows", unverified, 0,
      unverifiedMonths.length + " statement month" +
      (unverifiedMonths.length === 1 ? "" : "s") + " use CSV or manual data.",
      null, null
    ));

    var panel = document.querySelector(".quality-panel");
    if (!shown && sound) {
      clear(wrap);          // drop the intro that was staged above
      panel.classList.add("hidden");
      return;
    }
    panel.classList.remove("hidden");
    if (sound) {
      status.textContent = shown + " review item" + (shown === 1 ? "" : "s");
    }
    wrap.appendChild(grid);
    wrap.appendChild(el("p", "quality-note",
      "Only unresolved items appear here. " +
      (editor.available
        ? "Open a transaction to confirm its owner or mark a check as recognized."
        : "Start the local editor to save review decisions.")));
  }

  // ---------- Overview ----------

  function renderKpis() {
    var wrap = document.getElementById("kpis");
    clear(wrap);
    var m = state.month;
    var spend = cardSpend(m);
    var income = incomeFor(m);
    var invested = investedFor(m);

    var idx = data.months.indexOf(m);
    var comparison = null;
    if (idx > 0) {
      var prev = cardSpend(data.months[idx - 1]);
      if (prev > 0) {
        var pc = ((spend - prev) / prev) * 100;
        comparison = (pc >= 0 ? "+" : "") + pc.toFixed(1) + "% vs " +
          statementLabel(data.months[idx - 1]);
      }
    }
    var cycle = statementRange(m);
    var note = cycle + (cycle && comparison ? " · " : "") + (comparison || "");

    wrap.appendChild(metric("Income", income ? fmt0(income) : "—",
      income ? "salary credited" : "no statement", null, "wallet"));
    var spendCard = metric("Card statement spending", fmt0(spend), note, null, "card");
    makeActionable(spendCard, "View " + statementLabel(m) + " transactions", function () {
      openTransactions({ month: m });
    });
    wrap.appendChild(spendCard);
    wrap.appendChild(metric("Invested", invested ? fmt0(invested) : "—",
      hasAccount(m) ? "moved to investments" : "no statement", null, "up"));
    if (income > 0) {
      var left = income - spend;
      wrap.appendChild(metric("After card spending", fmt0(left),
        Math.round((left / income) * 100) + "% of income" +
        (invested > left ? ", investing drew on savings" : ""),
        left >= 0 ? "good" : "bad", "coins"));
    } else {
      wrap.appendChild(metric("After card spending", "—", "needs statement", null, "coins"));
    }
  }

  // "spent" is every non-wealth withdrawal, card-bill payments and transfers to
  // your own accounts included, so the chart plots it as "Outflows" rather than
  // spending. The KPI tiles keep their own, narrower card-spending figure.
  function allocationFor(month) {
    var moneyIn = 0, invested = 0, spent = 0;
    accountFor(month).forEach(function (t) {
      if (t.direction === "deposit") { moneyIn += t.amount; return; }
      if (WEALTH[t.flow]) invested += t.amount;
      else spent += t.amount;
    });
    return {
      month: month,
      income: incomeFor(month),
      moneyIn: moneyIn,
      invested: invested,
      spent: spent,
      saved: moneyIn - invested - spent
    };
  }

  var RANGES = [
    { key: "6", label: "6 months", count: 6 },
    { key: "12", label: "12 months", count: 12 },
    { key: "24", label: "24 months", count: 24 },
    { key: "all", label: "All", count: 0 }
  ];

  function renderStacked() {
    var wrap = document.getElementById("stacked");
    var pills = document.getElementById("range-pills");
    clear(pills);
    RANGES.forEach(function (r) {
      var b = el("button", "pill" + (r.key === state.range ? " active" : ""), r.label);
      setPressed(b, r.key === state.range);
      b.addEventListener("click", function () { state.range = r.key; renderStacked(); });
      pills.appendChild(b);
    });

    var months = account.months.slice();
    var idx = months.indexOf(state.month);
    if (idx === -1) {
      // Selected month has no statement; show everything up to it.
      months = months.filter(function (m) { return m <= state.month; });
      idx = months.length - 1;
    }
    var range = RANGES.filter(function (r) { return r.key === state.range; })[0] || RANGES[0];
    var window_ = range.count
      ? months.slice(Math.max(0, idx - range.count + 1), idx + 1)
      : months.slice(0, idx + 1);

    window.Charts.divergingMonths(wrap, window_.map(allocationFor), {
      activeMonth: state.month,
      visible: state.series,
      onMonth: function (month) {
        // Account-only months (2022-12, 2023-01) have no card statement; the
        // card ledger would render empty with a dangling period label, so
        // open the bank view for those instead.
        openTransactions(
          data.months.indexOf(month) === -1
            ? { source: "bank", month: month,
                period: { mode: "month", year: month.slice(0, 4), month: month.slice(5) } }
            : { month: month }
        );
      },
      onToggle: function (key) {
        // Clicking a series isolates it; clicking the isolated one brings the rest back.
        var shown = Object.keys(state.series).filter(function (k) {
          return state.series[k] !== false;
        });
        var alreadyAlone = shown.length === 1 && shown[0] === key;
        Object.keys(state.series).forEach(function (k) {
          state.series[k] = alreadyAlone ? true : k === key;
        });
        renderStacked();
      }
    });
  }

  function renderCategories() {
    var wrap = document.getElementById("category-bars");
    clear(wrap);
    var cats = {};
    cardTx(state.month).forEach(function (t) { cats[t.category] = (cats[t.category] || 0) + signed(t); });
    var names = Object.keys(cats).sort(function (a, b) { return cats[b] - cats[a]; });
    var max = names.length ? Math.max(cats[names[0]], 1) : 1;
    document.getElementById("cat-hint").textContent =
      statementLabel(state.month) + " · " + statementRange(state.month);
    if (!names.length) {
      wrap.appendChild(emptyState(
        "No spending in this statement",
        "Choose another statement month to see its category mix.",
        "pie"
      ));
      return;
    }
    names.forEach(function (name, i) {
      var row = el("div", "cat-row");
      row.appendChild(el("span", "name", name));
      var track = el("div", "track");
      var fill = el("div", "fill");
      fill.style.width = Math.max(1, Math.round((Math.max(cats[name], 0) / max) * 100)) + "%";
      fill.style.background = categoryColor(name);
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "amt", fmt(cats[name])));
      wrap.appendChild(row);
    });
  }

  function renderOutflows() {
    var wrap = document.getElementById("outflow-breakdown");
    clear(wrap);
    var hint = document.getElementById("flow-hint");
    if (!hasAccount(state.month)) {
      hint.textContent = "";
      wrap.appendChild(emptyState(
        "No account statement for " + monthLabel(state.month),
        "Card spending is still available; account outflows need the matching UOB ONE statement.",
        "wallet"
      ));
      return;
    }
    hint.textContent = monthLabel(state.month);
    var flows = {};
    accountFor(state.month).forEach(function (t) {
      if (t.direction !== "withdrawal") return;
      flows[t.flow] = (flows[t.flow] || 0) + t.amount;
    });
    var names = Object.keys(flows).sort(function (a, b) { return flows[b] - flows[a]; });
    if (!names.length) {
      wrap.appendChild(emptyState(
        "No outflows recorded",
        "This account statement contains no parsed withdrawals.",
        "swap"
      ));
      return;
    }
    var total = 0;
    names.forEach(function (n) { total += flows[n]; });

    var bar = el("div", "flow-bar");
    names.forEach(function (n) {
      var seg = el("div", "flow-seg " + (WEALTH[n] ? "wealth" : "spend"));
      seg.style.width = ((flows[n] / total) * 100) + "%";
      seg.title = n + " " + fmt0(flows[n]);
      bar.appendChild(seg);
    });
    wrap.appendChild(bar);

    var list = el("div", "flow-list");
    names.forEach(function (n) {
      var row = el("div", "flow-row");
      var left = el("span", "");
      left.appendChild(el("span", "dot " + (WEALTH[n] ? "wealth" : "spend")));
      left.appendChild(document.createTextNode(n));
      row.appendChild(left);
      row.appendChild(el("span", "amt", fmt0(flows[n])));
      list.appendChild(row);
    });
    wrap.appendChild(list);
    wrap.appendChild(el("p", "note", "Investments and savings are transfers, not spending."));
  }

  var INSIGHT_ICONS = { up: "up", down: "down", repeat: "repeat", percent: "percent",
    wallet: "wallet", receipt: "receipt", users: "users", invest: "coins",
    card: "card", user: "user" };

  function insightAction(item) {
    var title = item.title || "";
    // An insight that counted specific rows opens exactly those rows. The old
    // free-text fallback searched for a display alias that never appears in
    // statement text, so the drill-down under-matched its own headline count.
    if (item.ids && item.ids.length) {
      return function () {
        openTransactions({
          month: state.month,
          ids: item.ids,
          filterLabel: item.filterLabel || title,
          period: { mode: "all", year: state.period.year, month: state.period.month }
        });
      };
    }
    var categories = [
      "Food & dining", "Transport", "Shopping", "Games", "Insurance",
      "Subscriptions", "Groceries", "Healthcare", "Travel"
    ];
    var matchedCategory = null;
    for (var i = 0; i < categories.length; i += 1) {
      if (title.indexOf(categories[i]) !== -1) {
        matchedCategory = categories[i];
        break;
      }
    }
    if (matchedCategory) {
      return function () {
        openTransactions({ month: state.month, category: matchedCategory });
      };
    }
    if (/ran .*typical month/i.test(title)) {
      return function () { openTransactions({ month: state.month }); };
    }
    if (/salary/i.test(title)) {
      return function () { setTab("income"); };
    }
    if (/Yx/i.test(title)) {
      return function () { setTab("split"); };
    }
    if (/^Largest charge:/i.test(title) && item.detail) {
      var description = item.detail.split(" on ")[0];
      return function () {
        openTransactions({ month: state.month, search: description });
      };
    }
    var merchant = title.match(/^([^:]+):/);
    if (merchant) {
      return function () {
        openTransactions({
          month: state.month,
          search: merchant[1],
          period: { mode: "all", year: state.period.year, month: state.period.month }
        });
      };
    }
    return null;
  }

  function insightCard(item) {
    var card = el("div", "insight " + item.kind);
    var badge = el("span", "insight-icon");
    badge.appendChild(icon(INSIGHT_ICONS[item.icon] || "bulb"));
    card.appendChild(badge);
    var body = el("div", "insight-body");
    body.appendChild(el("p", "insight-title", item.title));
    body.appendChild(el("p", "insight-detail", item.detail));
    card.appendChild(body);
    var action = insightAction(item);
    if (action) makeActionable(card, "Explore: " + item.title, action);
    return card;
  }

  function renderInsights() {
    var items = window.Insights.build(data, state.month, account);
    var top = document.getElementById("overview-insights");
    clear(top);
    items.slice(0, 3).forEach(function (i) { top.appendChild(insightCard(i)); });
    if (!items.length) top.appendChild(emptyState(
      "Nothing unusual this month",
      "Spending and income stayed close to their recent patterns.",
      "bulb"
    ));
  }

  function renderSpendingSummary() {
    var wrap = document.getElementById("spending-summary");
    var panel = wrap.closest(".spending-summary-panel");
    var summary = window.Insights.summarize(data, state.month);
    wrap.textContent = summary.text;
    panel.classList.remove("warn", "good", "info");
    panel.classList.add(summary.kind || "info");
  }

  // ---------- Income ----------

  function renderIncome() {
    var wrap = document.getElementById("income-kpis");
    clear(wrap);
    var steps = data.salarySteps || [];
    var years = data.salaryYears || [];
    var latest = steps[steps.length - 1];
    var prev = steps[steps.length - 2];

    if (latest) {
      var note = null, tone = null;
      if (prev) {
        var pc = ((latest.amount - prev.amount) / prev.amount) * 100;
        note = (pc >= 0 ? "+" : "") + pc.toFixed(1) + "% from " + fmt0(prev.amount);
        tone = pc >= 0 ? "good" : "bad";
      }
      var c = metric("Current salary", fmt0(latest.amount), note, null, "wallet");
      if (tone) c.querySelector(".delta").className = "delta " + (tone === "good" ? "down" : "up");
      wrap.appendChild(c);
      wrap.appendChild(metric("Effective from", monthLabel(latest.from),
        latest.note || "monthly gross", null, "calendar"));
    }
    var lastYear = years[years.length - 1];
    if (lastYear) {
      wrap.appendChild(metric(lastYear.year + " income", fmt0(lastYear.income),
        lastYear.growth ? "+" + ((lastYear.growth - 1) * 100).toFixed(1) + "% on " + (lastYear.year - 1) : null,
        null, "up"));
      wrap.appendChild(metric(lastYear.year + " tax", fmt0(lastYear.tax),
        ((lastYear.tax / lastYear.income) * 100).toFixed(1) + "% effective rate", null, "receipt"));
    }

    // Salary step timeline
    var stepWrap = document.getElementById("salary-steps");
    clear(stepWrap);
    var maxSalary = 1;
    steps.forEach(function (s) { maxSalary = Math.max(maxSalary, s.amount); });
    steps.slice().reverse().forEach(function (s, i, arr) {
      var row = el("div", "step-row");
      row.appendChild(el("span", "step-date", monthLabel(s.from)));
      var track = el("div", "track");
      var fill = el("div", "fill");
      fill.style.width = Math.round((s.amount / maxSalary) * 100) + "%";
      fill.style.background = i === 0 ? "var(--bar-1)" : "var(--bar-3)";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "amt", fmt0(s.amount)));
      var next = arr[i + 1];
      var delta = next ? ((s.amount - next.amount) / next.amount) * 100 : null;
      var d = el("span", "step-delta " + (delta === null ? "" : delta >= 0 ? "pos" : "neg"),
        delta === null ? "" : (delta >= 0 ? "+" : "") + delta.toFixed(1) + "%");
      row.appendChild(d);
      stepWrap.appendChild(row);
    });

    // Annual table
    var yearWrap = document.getElementById("salary-years");
    clear(yearWrap);
    var table = el("table", "mini");
    var head = document.createElement("tr");
    ["Year", "Income", "Growth", "Tax"].forEach(function (h, i) {
      var th = el("th", i ? "num" : "", h);
      head.appendChild(th);
    });
    table.appendChild(head);
    years.forEach(function (y) {
      var tr = document.createElement("tr");
      tr.appendChild(el("td", "", String(y.year)));
      tr.appendChild(el("td", "num", fmt0(y.income)));
      tr.appendChild(el("td", "num " + (y.growth ? "pos" : ""),
        y.growth ? "+" + ((y.growth - 1) * 100).toFixed(1) + "%" : "—"));
      tr.appendChild(el("td", "num", y.tax ? fmt0(y.tax) : "—"));
      table.appendChild(tr);
    });
    yearWrap.appendChild(table);

    // Monthly credits
    var monthsWrap = document.getElementById("income-months");
    clear(monthsWrap);
    var series = account.months.slice(-18).map(function (m) {
      return { month: m, income: incomeFor(m) };
    }).filter(function (r) { return r.income > 0; });
    var maxInc = 1;
    series.forEach(function (r) { maxInc = Math.max(maxInc, r.income); });
    var base = series.length ? Math.min.apply(null, series.map(function (r) { return r.income; })) : 0;
    series.slice().reverse().forEach(function (r) {
      var row = el("div", "cat-row");
      row.appendChild(el("span", "name", monthLabel(r.month)));
      var track = el("div", "track");
      var fill = el("div", "fill");
      fill.style.width = Math.round((r.income / maxInc) * 100) + "%";
      fill.style.background = r.income > base * 1.4 ? "var(--green)" : "var(--bar-3)";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "amt", fmt0(r.income)));
      monthsWrap.appendChild(row);
    });
    if (!series.length) monthsWrap.appendChild(emptyState(
      "No salary credits found",
      "Import the matching account statements to populate monthly income.",
      "wallet"
    ));
  }

  // ---------- Key spending ----------

  // Grab and Foodpanda straddle two categories, so this one is matched on the
  // merchant string rather than the category.
  var DELIVERY_RIDES = /GRAB|FOOD ?PANDA|FP\*FOOD/i;

  var KEY_METRICS = [
    {
      label: "Food & dining", icon: "food", category: "Food & dining",
      match: function (t) { return t.category === "Food & dining"; }
    },
    {
      label: "Transport", icon: "bus", category: "Transport",
      match: function (t) { return t.category === "Transport"; }
    },
    {
      label: "Grab + Foodpanda", icon: "bike", reviewMode: "delivery-rides",
      match: function (t) { return DELIVERY_RIDES.test(t.description); }
    },
    {
      label: "Games", icon: "gamepad", category: "Games",
      match: function (t) { return t.category === "Games"; }
    }
  ];

  function median(values) {
    if (!values.length) return 0;
    var s = values.slice().sort(function (a, b) { return a - b; });
    var mid = Math.floor(s.length / 2);
    return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
  }

  function metricTotal(month, metric) {
    var total = 0;
    data.transactions.forEach(function (t) {
      if (t.month !== month || EXCLUDED[t.category]) return;
      if (metric.match(t)) total += signed(t);
    });
    return total;
  }

  function renderKeyMetrics() {
    var wrap = document.getElementById("key-metrics");
    clear(wrap);
    document.getElementById("key-hint").textContent =
      statementLabel(state.month) + " against your typical statement";

    var idx = data.months.indexOf(state.month);
    var base = data.months.slice(Math.max(0, idx - 6), idx);
    var last12 = data.months.slice(Math.max(0, idx - 11), idx + 1);

    KEY_METRICS.forEach(function (metric) {
      var value = metricTotal(state.month, metric);
      var typical = median(base.map(function (m) { return metricTotal(m, metric); }));
      var yearTotal = 0;
      last12.forEach(function (m) { yearTotal += metricTotal(m, metric); });

      var card = el("div", "kpi");
      var head = el("p", "label");
      head.appendChild(icon(metric.icon));
      head.appendChild(document.createTextNode(metric.label));
      card.appendChild(head);
      card.appendChild(el("p", "value", fmt0(value)));

      if (typical > 0) {
        var diff = value - typical;
        var pc = (diff / typical) * 100;
        var tone = Math.abs(pc) < 5 ? "" : diff > 0 ? "up" : "down";
        var text;
        if (Math.abs(pc) < 5) {
          text = "in line with typical " + fmt0(typical);
        } else if (typical < 25) {
          // A few dollars of baseline turns any change into a silly percentage.
          text = fmt0(Math.abs(diff)) + (diff > 0 ? " above" : " below") +
            " typical " + fmt0(typical);
        } else {
          text = (pc > 0 ? "+" : "") + pc.toFixed(0) + "% vs typical " + fmt0(typical);
        }
        card.appendChild(el("p", "delta " + tone, text));
      } else if (value > 0) {
        card.appendChild(el("p", "delta", "no recent baseline"));
      } else {
        card.appendChild(el("p", "delta", "nothing this month"));
      }
      card.appendChild(el("p", "delta sub", fmt0(yearTotal) + " over " + last12.length + " months"));
      makeActionable(card, "View " + metric.label + " transactions for " +
        statementLabel(state.month), function () {
        openTransactions({
          month: state.month,
          category: metric.category || "All",
          reviewMode: metric.reviewMode || null
        });
      });
      wrap.appendChild(card);
    });
  }

  // ---------- Insurance ----------

  var INSURANCE_BENEFIT_LABELS = {
    death: "Death",
    tpd: "TPD",
    earlyCriticalIllness: "Early CI",
    criticalIllness: "Critical illness",
    disabilityIncome: "Disability income",
    personalAccident: "Personal accident",
    hospitalSurgicalAnnualLimit: "Hospital annual limit"
  };
  var INSURANCE_TYPE_LABELS = {
    AE: "Endowment",
    END: "Endowment",
    WL: "Whole life",
    INV: "Investment",
    PA: "Personal accident"
  };

  function insuranceTypeLabel(value) {
    return INSURANCE_TYPE_LABELS[value] || value || "Not stated";
  }
  function insurancePeople() {
    return data && data.insurance && Array.isArray(data.insurance.people)
      ? data.insurance.people : [];
  }
  function selectedInsurancePeople() {
    var people = insurancePeople();
    if (state.insurancePerson === "all") return people;
    return people.filter(function (person) { return person.id === state.insurancePerson; });
  }
  function insurancePolicies() {
    var policies = [];
    selectedInsurancePeople().forEach(function (person) {
      person.policies.forEach(function (policy) {
        if (policy.hiddenInRegister === true) return;
        if (policy.coverageOnly === true && state.insurancePerson === "all") return;
        policies.push({ person: person, policy: policy });
      });
    });
    return policies;
  }
  function insuranceTotals() {
    if (state.insurancePerson === "all") {
      return (data.insurance && data.insurance.totals) || {
        policies: 0, activePolicies: 0, maturedPolicies: 0, lapsedPolicies: 0,
        annualCashPremium: 0, annualCpfPremium: 0, monthlyEquivalent: 0
      };
    }
    var person = selectedInsurancePeople()[0];
    return person ? person.totals : {
      policies: 0, activePolicies: 0, maturedPolicies: 0, lapsedPolicies: 0,
      annualCashPremium: 0, annualCpfPremium: 0, monthlyEquivalent: 0
    };
  }
  function insuranceChargeRows() {
    var selected = selectedInsurancePeople();
    var owners = {};
    selected.forEach(function (person) { owners[person.owner || person.name] = true; });
    return data.transactions.filter(function (t) {
      if (t.category !== "Insurance") return false;
      var year = (t.date || t.month).slice(0, 4);
      if (state.insuranceYear && year !== state.insuranceYear) return false;
      return state.insurancePerson === "all" || owners[t.owner];
    });
  }
  function insuranceNet(rows) {
    return roundMoney(rows.reduce(function (total, t) { return total + signed(t); }, 0));
  }
  function policyPaymentAmount(policy) {
    var premiums = policy.premiums || {};
    return roundMoney(Number(premiums.cashWithValue || 0) +
      Number(premiums.cashWithoutValue || 0));
  }
  function insuranceCutoff(year) {
    var latestYear = data.months[data.months.length - 1].slice(0, 4);
    if (year < latestYear) return year + "-12-31";
    var dates = data.transactions.filter(function (t) {
      return t.date && t.date.slice(0, 4) === year;
    }).map(function (t) { return t.date.slice(0, 10); }).sort();
    return dates.length ? dates[dates.length - 1] : year + "-12-31";
  }
  function scheduledPolicyPayments(policy, cutoff) {
    var amount = policyPaymentAmount(policy);
    var frequency = String(policy.premiums && policy.premiums.frequency || "");
    if (!amount || !policy.startDate || !frequency) return { count: 0, amount: 0 };
    var year = parseInt(cutoff.slice(0, 4), 10);
    var cutoffMonth = parseInt(cutoff.slice(5, 7), 10);
    var cutoffDay = parseInt(cutoff.slice(8, 10), 10);
    var startYear = parseInt(policy.startDate.slice(0, 4), 10);
    var startMonth = parseInt(policy.startDate.slice(5, 7), 10);
    var dueDay = parseInt(policy.startDate.slice(8, 10), 10);
    if (startYear > year || policy.startDate > cutoff) return { count: 0, amount: 0 };
    var count = 0;
    if (frequency === "Monthly") {
      var firstMonth = startYear === year ? startMonth : 1;
      var lastMonth = cutoffMonth - (cutoffDay < dueDay ? 1 : 0);
      count = Math.max(0, lastMonth - firstMonth + 1);
    } else if (frequency === "Annual") {
      count = (startMonth < cutoffMonth ||
        (startMonth === cutoffMonth && dueDay <= cutoffDay)) ? 1 : 0;
    }
    return { count: count, amount: roundMoney(count * amount) };
  }
  function policyMatchesDescription(policy, description) {
    var text = String(description || "").toUpperCase();
    var number = String(policy.policyNumber || "").replace(/[^0-9A-Z]/gi, "").toUpperCase();
    if (number && !/^NODETAILS$/.test(number) && text.replace(/[^0-9A-Z]/g, "").indexOf(number) !== -1) {
      return true;
    }
    var company = String(policy.company || "").toUpperCase();
    if (company.indexOf("PRUDENTIAL") !== -1) return text.indexOf("PRUDENTIAL") !== -1;
    if (company.indexOf("TOKIO") !== -1) return text.indexOf("TOKIO") !== -1;
    if (company.indexOf("GREAT EASTERN") !== -1) return text.indexOf("GREAT EAST") !== -1;
    if (company.indexOf("SINGLIFE") !== -1 || company.indexOf("AVIVA") !== -1) {
      return text.indexOf("SINGLIFE") !== -1 || text.indexOf("AVIVA") !== -1;
    }
    return false;
  }
  function insurancePolicyStatementHistory(policyEntries) {
    var history = {};
    var active = policyEntries.filter(function (entry) {
      return String(entry.policy.status || "In Force") === "In Force" &&
        policyPaymentAmount(entry.policy) > 0;
    });
    var rows = data.transactions.filter(function (transaction) {
      return transaction.category === "Insurance" && transaction.type === "debit";
    }).sort(function (a, b) {
      return String(b.date || b.month).localeCompare(String(a.date || a.month));
    });
    rows.forEach(function (transaction) {
      var description = String(transaction.description || "");
      var normalizedDescription = description.replace(/[^0-9A-Z]/gi, "").toUpperCase();
      var sameOwner = active.filter(function (entry) {
        return !entry.person.owner || entry.person.owner === transaction.owner;
      });
      var direct = sameOwner.filter(function (entry) {
        var number = String(entry.policy.policyNumber || "")
          .replace(/[^0-9A-Z]/gi, "").toUpperCase();
        return number && !/^NODETAILS$/.test(number) &&
          normalizedDescription.indexOf(number) !== -1;
      });
      var matchType = "policy number";
      var candidates = direct;
      if (!candidates.length) {
        candidates = sameOwner.filter(function (entry) {
          return Math.abs(policyPaymentAmount(entry.policy) - transaction.amount) < 0.01 &&
            policyMatchesDescription(entry.policy, description);
        });
        matchType = "insurer and amount";
      }
      if (candidates.length !== 1) return;
      var policy = candidates[0].policy;
      if (!history[policy.id]) history[policy.id] = [];
      history[policy.id].push({
        transaction: transaction,
        matchType: matchType,
        amountChanged: Math.abs(policyPaymentAmount(policy) - transaction.amount) >= 0.01
      });
    });
    return history;
  }
  function reconcileInsurance(rows) {
    var cutoff = insuranceCutoff(state.insuranceYear);
    var policies = insurancePolicies().filter(function (entry) {
      return entry.policy.reconcileWithImportedStatements !== false;
    }).map(function (entry) {
      var scheduled = scheduledPolicyPayments(entry.policy, cutoff);
      return {
        person: entry.person,
        policy: entry.policy,
        scheduledCount: scheduled.count,
        scheduledAmount: scheduled.amount,
        matchedCount: 0,
        matchedAmount: 0,
        transactions: []
      };
    });
    var debits = rows.filter(function (t) { return t.type === "debit"; });
    var unmatched = [];
    debits.forEach(function (transaction) {
      var direct = policies.filter(function (entry) {
        var number = String(entry.policy.policyNumber || "").replace(/[^0-9A-Z]/gi, "").toUpperCase();
        return number && !/^NODETAILS$/.test(number) &&
          String(transaction.description || "").replace(/[^0-9A-Z]/gi, "").toUpperCase()
            .indexOf(number) !== -1;
      });
      var candidates = direct.length ? direct : policies.filter(function (entry) {
        return Math.abs(policyPaymentAmount(entry.policy) - transaction.amount) < 0.01 &&
          policyMatchesDescription(entry.policy, transaction.description);
      });
      if (candidates.length === 1) {
        candidates[0].matchedCount += 1;
        candidates[0].matchedAmount = roundMoney(candidates[0].matchedAmount + transaction.amount);
        candidates[0].transactions.push(transaction);
      } else {
        unmatched.push(transaction);
      }
    });
    var expectedAmount = roundMoney(policies.reduce(function (total, entry) {
      return total + entry.scheduledAmount;
    }, 0));
    var expectedCount = policies.reduce(function (total, entry) {
      return total + entry.scheduledCount;
    }, 0);
    var matchedAmount = roundMoney(policies.reduce(function (total, entry) {
      return total + entry.matchedAmount;
    }, 0));
    var matchedCount = policies.reduce(function (total, entry) {
      return total + Math.min(entry.matchedCount, entry.scheduledCount);
    }, 0);
    var missingPolicies = policies.filter(function (entry) {
      return entry.scheduledAmount - entry.matchedAmount > 0.01;
    });
    var unmatchedAmount = roundMoney(unmatched.reduce(function (total, t) {
      return total + t.amount;
    }, 0));
    var actualAmount = insuranceNet(rows);
    var refunds = rows.filter(function (t) { return t.type !== "debit"; });
    var tallies = Math.abs(actualAmount - expectedAmount) < 0.01 &&
      !unmatched.length && !missingPolicies.length;
    return {
      cutoff: cutoff,
      expectedAmount: expectedAmount,
      expectedCount: expectedCount,
      actualAmount: actualAmount,
      difference: roundMoney(actualAmount - expectedAmount),
      matchedAmount: matchedAmount,
      matchedCount: matchedCount,
      unmatchedAmount: unmatchedAmount,
      unmatchedCount: unmatched.length,
      unmatched: unmatched,
      refunds: refunds,
      policies: policies,
      missingPolicies: missingPolicies,
      refundCount: refunds.length,
      tallies: tallies
    };
  }
  function renderInsuranceReconciliation(rows, result) {
    result = result || reconcileInsurance(rows);
    var wrap = document.getElementById("insurance-reconciliation");
    clear(wrap);
    wrap.className = "insurance-reconciliation " + (result.tallies ? "tallies" : "mismatch");

    var cadence = result.policies.reduce(function (summary, entry) {
      var frequency = String(entry.policy.premiums && entry.policy.premiums.frequency || "");
      if (frequency !== "Monthly" && frequency !== "Annual") return summary;
      summary[frequency].due += entry.scheduledCount;
      summary[frequency].matched += Math.min(entry.matchedCount, entry.scheduledCount);
      return summary;
    }, { Monthly: { due: 0, matched: 0 }, Annual: { due: 0, matched: 0 } });
    function cadenceParts(key) {
      var parts = [];
      var monthlyCount = cadence.Monthly[key];
      var annualCount = cadence.Annual[key];
      if (monthlyCount) parts.push(monthlyCount + " monthly instalment" + (monthlyCount === 1 ? "" : "s"));
      if (annualCount) parts.push(annualCount + " annual premium" + (annualCount === 1 ? "" : "s"));
      return parts;
    }
    function compactCadence(key) {
      var parts = [];
      if (cadence.Monthly[key]) parts.push("Monthly " + cadence.Monthly[key]);
      if (cadence.Annual[key]) parts.push("Annual " + cadence.Annual[key]);
      return parts.join(" · ");
    }
    var scheduledCadence = cadenceParts("due");
    var matchedCadence = cadenceParts("matched");

    var status = el("div", "insurance-reconciliation-status");
    var statusLine = el("div", "insurance-status-line");
    statusLine.appendChild(el("span", "insurance-status-dot"));
    statusLine.appendChild(el("strong", "", result.tallies ? "Tallies" : "Doesn't tally"));
    status.appendChild(statusLine);
    var difference = result.difference;
    status.appendChild(el("p", "", result.tallies
      ? "All charges due through " + dateLabel(result.cutoff) + " match: " +
        (scheduledCadence.length ? scheduledCadence.join(" and ") : "no cash premiums due") + "."
      : fmt(Math.abs(difference)) + (difference < 0 ? " below" : " above") +
        " the scheduled amount through " + dateLabel(result.cutoff) + "."));
    wrap.appendChild(status);

    [
      ["Scheduled cash", fmt(result.expectedAmount), compactCadence("due") || "No payments due"],
      ["On statements", fmt(result.actualAmount), rows.length + " charges, net"],
      ["Matched", fmt(result.matchedAmount), compactCadence("matched") || "No scheduled payments"]
    ].forEach(function (item) {
      var metricNode = el("div", "insurance-reconciliation-metric");
      metricNode.appendChild(el("span", "", item[0]));
      metricNode.appendChild(el("strong", "", item[1]));
      metricNode.appendChild(el("small", "", item[2]));
      wrap.appendChild(metricNode);
    });

    if (!result.tallies) {
      var detail = el("p", "insurance-reconciliation-detail");
      var parts = [];
      if (result.missingPolicies.length) {
        var missing = result.missingPolicies.slice(0, 3).map(function (entry) {
          return entry.policy.plan + " " + fmt(roundMoney(entry.scheduledAmount - entry.matchedAmount));
        });
        if (result.missingPolicies.length > 3) {
          missing.push("+" + (result.missingPolicies.length - 3) + " more");
        }
        parts.push("Missing scheduled: " + missing.join(", "));
      }
      if (result.unmatchedCount) {
        var unlinked = result.unmatched.slice(0, 3).map(function (transaction) {
          return transactionName(transaction) + " " + fmt(transaction.amount);
        });
        if (result.unmatchedCount > 3) unlinked.push("+" + (result.unmatchedCount - 3) + " more");
        parts.push("Unlinked statement charges: " + unlinked.join(", "));
      }
      if (result.refundCount) {
        parts.push(result.refundCount + " refund" + (result.refundCount === 1 ? "" : "s") +
          " included in the net amount");
      }
      detail.textContent = parts.join(" · ") + ".";
      wrap.appendChild(detail);
    }
  }
  function policyBenefitEntries(policy) {
    return Object.keys(INSURANCE_BENEFIT_LABELS).filter(function (key) {
      return Number(policy.benefits && policy.benefits[key]) > 0;
    }).map(function (key) {
      return { key: key, label: INSURANCE_BENEFIT_LABELS[key], value: policy.benefits[key] };
    });
  }
  function policyBenefitSummary(policy) {
    if (String(policy.status || "In Force") !== "In Force") {
      return "Historical record · cover inactive";
    }
    var coveredPeople = {};
    (policy.components || []).forEach(function (component) {
      if (component.insuredPerson) coveredPeople[component.insuredPerson] = true;
    });
    var componentCount = (policy.components || []).filter(function (component) {
      return Boolean(component.insuredPerson);
    }).length;
    if (componentCount) {
      return componentCount + " active policies · " + Object.keys(coveredPeople).join(" + ");
    }
    var benefits = policyBenefitEntries(policy);
    if (!benefits.length) return "No stated coverage";
    var shown = benefits.slice(0, 2).map(function (benefit) {
      return benefit.label + " " + fmt0(benefit.value);
    });
    if (benefits.length > 2) shown.push("+" + (benefits.length - 2) + " more");
    return shown.join(" · ");
  }
  function policyCyclePremium(policy) {
    var status = String(policy.status || "In Force");
    var valuation = policy.valuation || {};
    if (status === "Matured") {
      return valuation.maturityValue ? "Matured · " + fmt(valuation.maturityValue) : "Matured";
    }
    if (status === "Lapsed") return "Lapsed";
    if (policy.coverageOnly === true) {
      return policy.premiumPaidBy ? "Paid under " + policy.premiumPaidBy : "Linked cover";
    }
    var premiums = policy.premiums || {};
    var amount = Number(premiums.cashWithValue || 0) + Number(premiums.cashWithoutValue || 0);
    if (!amount && Number(premiums.cpfAnnual || 0)) {
      return fmt(Number(premiums.cpfAnnual)) + " / year · MediSave";
    }
    if (!amount) return policy.oneOffPaid ? "One-off " + fmt0(policy.oneOffPaid) : "No recurring premium";
    var frequency = String(premiums.frequency || "").toLowerCase();
    var period = { monthly: "month", annual: "year", annually: "year", yearly: "year" }[frequency];
    var label = fmt(amount) + (period ? " / " + period : frequency ? " / " + frequency : "");
    if (policy.reconcileWithImportedStatements === false && policy.paymentMethod) {
      label += " · " + policy.paymentMethod;
    }
    return label;
  }
  function policyAnnualPremiumLabel(policy) {
    if (String(policy.status || "In Force") !== "In Force") return "—";
    if (policy.coverageOnly === true) {
      return policy.premiumPaidBy ? "Included under " + policy.premiumPaidBy : "Included elsewhere";
    }
    var cash = Number(policy.annualCashPremium || 0);
    var cpf = Number(policy.premiums && policy.premiums.cpfAnnual || 0);
    if (cash && cpf) return fmt(cash) + " + " + fmt(cpf) + " MediSave";
    if (cash) return fmt(cash);
    if (cpf) return fmt(cpf) + " MediSave";
    return "—";
  }
  function shortPolicyNumber(value) {
    var text = String(value || "");
    if (!text || /^no details$/i.test(text)) return "Policy number not recorded";
    return "Policy •••• " + text.slice(-4);
  }
  function policyVerification(policy) {
    var verification = policy && policy.verification || {};
    return verification.source && verification.checkedAt ? verification : null;
  }
  function verificationTitle(policy) {
    var verification = policyVerification(policy);
    return verification ? "Checked against " + verification.source + " on " +
      dateLabel(verification.checkedAt) : "";
  }
  function policyPlainSummary(policy) {
    if (policy.summary) return policy.summary;
    var type = insuranceTypeLabel(policy.type);
    var inactive = String(policy.status || "In Force") !== "In Force";
    if (inactive) {
      return "A historical " + type.toLowerCase() +
        " policy retained for your records. It is not included in current premiums or active coverage.";
    }
    if (/H&S Rider/i.test(type)) {
      return "A supplementary hospital rider intended to reduce part of your out-of-pocket cost when an eligible claim is paid under its linked medical plan.";
    }
    if (/H&S/i.test(type)) {
      return "A hospital and surgical plan intended to help pay eligible medical costs, subject to its ward entitlement, benefit limits and exclusions.";
    }
    if (/Term/i.test(type)) {
      return "A protection-focused policy for the recorded life, disability and illness benefits during its insured term. It is intended for cover rather than long-term savings.";
    }
    if (/Whole life/i.test(type)) {
      return "A long-term protection policy intended to provide life coverage beyond the premium-payment period, with the recorded supplementary benefits attached.";
    }
    if (/Endowment/i.test(type)) {
      return "A savings-oriented insurance policy that builds towards a future maturity or cash value while retaining a level of life protection.";
    }
    if (/Investment/i.test(type)) {
      return "A wealth-accumulation policy intended to grow a lump sum over its policy term, with returns and access governed by the recorded policy terms.";
    }
    if (/Personal accident/i.test(type)) {
      return "An accident-protection policy intended to pay the recorded benefits for eligible accidental injuries or events.";
    }
    return "An insurance policy retained in your portfolio for its recorded protection or savings benefits. Open the sections below for its premium, coverage and policy terms.";
  }
  function addInsuranceSummary(body, policy) {
    var section = el("section", "drawer-section insurance-policy-summary");
    section.appendChild(el("h3", "", "What this policy does"));
    section.appendChild(el("p", "", policyPlainSummary(policy)));
    var verification = policyVerification(policy);
    section.appendChild(el("small", "insurance-policy-summary-source", verification ?
      "Based on the insurer record checked " + dateLabel(verification.checkedAt) + "." :
      "Based on the recorded policy details; not yet checked against the insurer portal."));
    body.appendChild(section);
  }
  function insuranceBundleComponents(policy) {
    return (policy.components || []).filter(function (component) {
      return Boolean(component.insuredPerson);
    });
  }
  function addInsuranceBundleBreakdown(body, policy) {
    var components = insuranceBundleComponents(policy);
    if (!components.length) return;
    var section = el("section", "insurance-bundle-breakdown");
    var heading = el("div", "insurance-bundle-heading");
    var headingCopy = el("div", "");
    headingCopy.appendChild(el("strong", "", components.length + " active policies"));
    headingCopy.appendChild(el("small", "", "Verified in MySinglife · expanded by insured person"));
    heading.appendChild(headingCopy);
    section.appendChild(heading);

    var people = {};
    components.forEach(function (component) {
      var name = component.insuredPerson;
      if (!people[name]) people[name] = [];
      people[name].push(component);
    });
    var groups = el("div", "insurance-bundle-groups");
    Object.keys(people).forEach(function (name) {
      var items = people[name];
      var monthly = roundMoney(items.reduce(function (total, item) {
        return total + Number(item.premiumAmount || 0);
      }, 0));
      var group = el("section", "insurance-bundle-person");
      var head = el("div", "insurance-bundle-person-head");
      var identity = el("div", "");
      identity.appendChild(el("strong", "", name));
      identity.appendChild(el("small", "", items[0].relationship || "Insured person"));
      head.appendChild(identity);
      head.appendChild(el("span", "", fmt(monthly) + " / month"));
      group.appendChild(head);
      items.forEach(function (component) {
        var row = el("div", "insurance-bundle-item");
        var description = el("div", "");
        description.appendChild(el("strong", "", component.name));
        description.appendChild(el("small", "", [
          component.benefitLabel,
          component.sumAssured ? fmt0(component.sumAssured) + " cover" : ""
        ].filter(Boolean).join(" · ")));
        row.appendChild(description);
        row.appendChild(el("strong", "", fmt(component.premiumAmount || 0) + " / mo"));
        group.appendChild(row);
      });
      groups.appendChild(group);
    });
    section.appendChild(groups);

    var portalTotal = Number(policy.portalPremiumTotal || 0);
    var debitTotal = Number(policy.accountDebitAmount || 0);
    if (portalTotal || debitTotal) {
      var totals = el("div", "insurance-bundle-totals");
      totals.appendChild(el("span", "", "Portal-listed policy costs " +
        fmt(portalTotal) + " / month"));
      totals.appendChild(el("span", "", "Recorded DBS debit " + fmt(debitTotal) + " / month"));
      var difference = roundMoney(debitTotal - portalTotal);
      if (difference) {
        totals.appendChild(el("strong", "", "Unexplained difference " + fmt(difference)));
      }
      section.appendChild(totals);
    }
    body.appendChild(section);
  }
  function addInsuranceDetailSection(body, heading, rows) {
    var section = el("section", "drawer-section");
    section.appendChild(el("h3", "", heading));
    var meta = el("div", "drawer-meta");
    rows.filter(function (row) {
      return row[1] !== undefined && row[1] !== null && row[1] !== "";
    }).forEach(function (row) {
      meta.appendChild(drawerMetaRow(row[0], String(row[1]), row[2]));
    });
    section.appendChild(meta);
    body.appendChild(section);
  }
  function addInsuranceListSection(body, heading, items) {
    if (!items || !items.length) return;
    var section = el("section", "drawer-section");
    section.appendChild(el("h3", "", heading));
    var list = el("ul", "insurance-detail-list");
    items.forEach(function (item) { list.appendChild(el("li", "", item)); });
    section.appendChild(list);
    body.appendChild(section);
  }
  function insuranceInlineFacts(policy) {
    var facts = [];
    var status = String(policy.status || "In Force");
    if (status === "In Force") {
      facts.push(["Payment", policyCyclePremium(policy)]);
    } else {
      facts.push(["Status", status]);
    }

    var benefits = policyBenefitEntries(policy);
    var valuation = policy.valuation || {};
    if (benefits.length) {
      facts.push([benefits[0].label, fmt(benefits[0].value)]);
    } else if (valuation.maturityValue) {
      facts.push(["Maturity value", fmt(valuation.maturityValue)]);
    } else if (valuation.netSurrenderValue) {
      facts.push(["Net surrender value", fmt(valuation.netSurrenderValue)]);
    } else {
      facts.push(["Policy type", insuranceTypeLabel(policy.type)]);
    }

    if (status !== "In Force" && policy.statusDate) {
      facts.push([status + " on", dateLabel(policy.statusDate)]);
    } else if (policy.premiumEndDate) {
      facts.push(["Premiums end", dateLabel(policy.premiumEndDate)]);
    } else if (policy.coverExpiryDate) {
      facts.push(["Cover ends", dateLabel(policy.coverExpiryDate)]);
    } else if (policy.payableTerm) {
      facts.push(["Pay until", policy.payableTerm]);
    } else if (policy.premiumPaidToDate && status === "In Force") {
      facts.push(["Paid through", dateLabel(policy.premiumPaidToDate)]);
    }
    return facts.slice(0, 3);
  }
  function renderInsuranceDrawer(policy, person) {
    var title = document.getElementById("transaction-drawer-title");
    var eyebrow = document.getElementById("transaction-drawer-eyebrow");
    var body = document.getElementById("transaction-drawer-body");
    clear(body);
    title.textContent = policy.plan;
    eyebrow.textContent = person.name + " · Insurance policy";

    var summary = el("section", "drawer-summary");
    var annualTotal = Number(policy.annualCashPremium || 0) +
      Number(policy.premiums && policy.premiums.cpfAnnual || 0);
    var valuation = policy.valuation || {};
    var headline = policy.coverageOnly === true && policy.premiumPaidBy
      ? "Paid by " + policy.premiumPaidBy : annualTotal ? fmt(annualTotal) :
      valuation.maturityValue ? fmt(valuation.maturityValue) :
      policy.oneOffPaid ? fmt(policy.oneOffPaid) : "No active premium";
    summary.appendChild(el("strong", "drawer-amount", headline));
    var tags = el("div", "drawer-summary-tags");
    tags.appendChild(el("span", "drawer-owner", policy.coverageOnly === true ? "linked cover" :
      annualTotal ? "per year" :
      valuation.maturityValue ? "maturity value" : policy.oneOffPaid ? "single premium" : "historical"));
    if (Number(policy.premiums && policy.premiums.cpfAnnual || 0) &&
        !Number(policy.annualCashPremium || 0)) {
      tags.appendChild(el("span", "drawer-owner", "MediSave"));
    }
    tags.appendChild(el("span", String(policy.status || "In Force") === "In Force"
      ? "drawer-verified" : "drawer-owner", policy.status || "In Force"));
    if (policyVerification(policy)) {
      var verifiedTag = el("span", "drawer-verified", "✓ Insurer verified");
      verifiedTag.title = verificationTitle(policy);
      tags.appendChild(verifiedTag);
    }
    tags.appendChild(el("span", "drawer-owner", insuranceTypeLabel(policy.type)));
    summary.appendChild(tags);
    body.appendChild(summary);
    addInsuranceSummary(body, policy);

    var premiums = policy.premiums || {};
    addInsuranceDetailSection(body, "Policy", [
      ["Company", policy.company],
      ["Policy number", policy.policyNumber, true],
      ["Status", policy.status || "In Force"],
      ["Status date", policy.statusDate ? dateLabel(policy.statusDate) : ""],
      ["Start date", policy.startDate ? dateLabel(policy.startDate) : "Not stated"],
      ["Type", insuranceTypeLabel(policy.type)],
      ["Payable term", policy.payableTerm || "Not stated"],
      ["Premium end", policy.premiumEndDate ? dateLabel(policy.premiumEndDate) : ""],
      ["Cover expiry", policy.coverExpiryDate ? dateLabel(policy.coverExpiryDate) : ""]
    ]);
    if (policyVerification(policy)) {
      addInsuranceDetailSection(body, "Verification", [
        ["Result", "Verified against insurer record"],
        ["Source", policy.verification.source],
        ["Checked", dateLabel(policy.verification.checkedAt)]
      ]);
    }
    addInsuranceDetailSection(body, "Premiums", [
      ["Frequency", premiums.frequency || "Not recurring"],
      ["Payment method", policy.paymentMethod || "Not recorded"],
      ["Statement tracking", policy.coverageOnly === true && policy.premiumPaidBy
        ? "Premium tracked once under " + policy.premiumPaidBy + "'s bundle"
        : policy.reconcileWithImportedStatements === false
          ? "Outside imported UOB statements" : "Included when a matching UOB charge is found"],
      ["Paid to", policy.premiumPaidToDate ? dateLabel(policy.premiumPaidToDate) : ""],
      ["Basic premium", policy.basicPremium ? fmt(policy.basicPremium) : ""],
      ["Cash with value", fmt(Number(premiums.cashWithValue || 0))],
      ["Cash without value", fmt(Number(premiums.cashWithoutValue || 0))],
      ["Annual cash premium", fmt(policy.annualCashPremium || 0)],
      ["Monthly equivalent", fmt(policy.monthlyEquivalent || 0)],
      ["Annual CPF premium", fmt(Number(premiums.cpfAnnual || 0))],
      ["One-off amount paid", policy.oneOffPaid ? fmt(policy.oneOffPaid) : "None"]
    ]);
    addInsuranceDetailSection(body, "Policy structure", [
      ["Face value", policy.faceValue ? fmt(policy.faceValue) : ""],
      ["Base sum assured", policy.baseSumAssured ? fmt(policy.baseSumAssured) : ""],
      ["Multiplier", policy.multiplierBenefit || ""]
    ]);
    var benefitRows = policyBenefitEntries(policy).map(function (benefit) {
      return [benefit.label, fmt(benefit.value)];
    });
    if (!benefitRows.length) benefitRows.push(["Coverage", "No benefit amount recorded"]);
    addInsuranceDetailSection(body, "Coverage", benefitRows);
    if (policy.components && policy.components.length) {
      addInsuranceDetailSection(body, insuranceBundleComponents(policy).length ?
        "Included policies" : "Components and riders", policy.components.map(function (component) {
        var details = [];
        if (component.insuredPerson) details.push(component.insuredPerson);
        if (component.benefitLabel) details.push(component.benefitLabel);
        if (component.sumAssured) details.push("Sum assured " + fmt(component.sumAssured));
        if (component.premiumAmount) details.push("Premium " + fmt(component.premiumAmount) +
          (component.premiumFrequency ? " / " + component.premiumFrequency.toLowerCase() : ""));
        if (component.coverageEffectiveDate) details.push("effective " +
          dateLabel(component.coverageEffectiveDate));
        if (component.nextDueDate) details.push("next due " + dateLabel(component.nextDueDate));
        if (component.premiumEndDate) details.push("premium to " + dateLabel(component.premiumEndDate));
        if (component.coverExpiryDate) details.push("cover to " + dateLabel(component.coverExpiryDate));
        return [component.name, details.join(" · ") || component.status || "In Force"];
      }));
    }
    if (valuation.asOf || valuation.guaranteedBonus || valuation.grossSurrenderValue ||
        valuation.netSurrenderValue || valuation.maturityValue) {
      addInsuranceDetailSection(body, policy.status === "Matured" ? "Maturity" : "Current value", [
        ["As of", valuation.asOf ? dateLabel(valuation.asOf) : ""],
        ["Guaranteed bonus", fmt(Number(valuation.guaranteedBonus || 0))],
        ["Gross surrender", fmt(Number(valuation.grossSurrenderValue || 0))],
        ["Indebtedness", fmt(Number(valuation.indebtedness || 0))],
        ["Net surrender", fmt(Number(valuation.netSurrenderValue || 0))],
        ["Maturity value", valuation.maturityValue ? fmt(valuation.maturityValue) : ""]
      ]);
    }
    addInsuranceListSection(body, "Coverage details", policy.coverageNotes || []);
    if (policy.documents && policy.documents.length) {
      addInsuranceDetailSection(body, "Latest documents checked", policy.documents.map(function (document) {
        return [document.name, [document.type, document.date ? dateLabel(document.date) : ""]
          .filter(Boolean).join(" · ")];
      }));
    }
    addInsuranceDetailSection(body, "Notes", [
      ["Premium waiver", policy.premiumWaiver || "None recorded"],
      ["Remarks", policy.remarks || "None"]
    ]);
  }
  function openInsuranceDrawer(policy, person) {
    closeAuditHistory();
    var shell = document.getElementById("transaction-drawer-shell");
    if (editor.drawerCloseTimer) window.clearTimeout(editor.drawerCloseTimer);
    if (!editor.drawerTransactionId) editor.drawerLastFocus = document.activeElement;
    editor.drawerTransactionId = "insurance:" + policy.id;
    renderInsuranceDrawer(policy, person);
    shell.classList.remove("hidden");
    shell.setAttribute("aria-hidden", "false");
    document.body.classList.add("drawer-open");
    window.requestAnimationFrame(function () { shell.classList.add("is-open"); });
    document.getElementById("transaction-drawer-close").focus();
  }
  function renderInsuranceCoverage() {
    var wrap = document.getElementById("insurance-coverage");
    clear(wrap);
    var selected = selectedInsurancePeople();
    var groups = [
      { title: "Life & disability", icon: "shield", keys: ["death", "tpd", "disabilityIncome"] },
      { title: "Critical illness", icon: "target", keys: ["earlyCriticalIllness", "criticalIllness"] },
      { title: "Medical & accident", icon: "star", keys: ["hospitalSurgicalAnnualLimit", "personalAccident"] }
    ];
    selected.forEach(function (person) {
      var card = el("article", "insurance-coverage-card" +
        (selected.length === 1 ? " single" : ""));
      if (selected.length > 1) {
        var personHead = el("div", "insurance-coverage-person-head");
        personHead.appendChild(el("h3", "", person.name));
        personHead.appendChild(el("span", "", person.totals.policies + " policies"));
        card.appendChild(personHead);
      }
      var groupWrap = el("div", "insurance-benefit-groups");
      groups.forEach(function (group) {
        var populated = group.keys.filter(function (key) {
          return Number(person.coverage && person.coverage[key] || 0) > 0;
        });
        if (!populated.length) return;
        var section = el("section", "insurance-benefit-group");
        var head = el("div", "insurance-benefit-group-head");
        var badge = el("span", "insurance-benefit-icon");
        badge.appendChild(icon(group.icon));
        head.appendChild(badge);
        head.appendChild(el("h4", "", group.title));
        section.appendChild(head);
        populated.forEach(function (key) {
          var row = el("div", "insurance-coverage-metric");
          row.appendChild(el("span", "", INSURANCE_BENEFIT_LABELS[key]));
          row.appendChild(el("strong", "", fmt0(person.coverage[key])));
          section.appendChild(row);
        });
        groupWrap.appendChild(section);
      });
      if (!groupWrap.children.length) {
        groupWrap.appendChild(el("p", "empty", "No coverage amounts recorded"));
      }
      card.appendChild(groupWrap);
      wrap.appendChild(card);
    });
  }
  function renderPastInsurancePolicies(entries) {
    var list = document.getElementById("insurance-past-list");
    clear(list);
    entries.forEach(function (entry) {
      var policy = entry.policy;
      var card = el("article", "insurance-past-card");
      var head = el("div", "insurance-past-card-head");
      var identity = el("div", "insurance-past-card-identity");
      identity.appendChild(el("h3", "", policy.plan));
      identity.appendChild(el("span", "", policy.company + " · " + entry.person.name + " · " +
        shortPolicyNumber(policy.policyNumber)));
      head.appendChild(identity);
      head.appendChild(el("span", "insurance-past-status", policy.status || "Inactive"));
      card.appendChild(head);
      card.appendChild(el("p", "insurance-past-summary", policyPlainSummary(policy)));

      var facts = el("div", "insurance-policy-inline-facts insurance-past-facts");
      insuranceInlineFacts(policy).forEach(function (fact) {
        var item = el("span", "insurance-policy-inline-fact");
        item.appendChild(el("small", "", fact[0]));
        item.appendChild(el("strong", "", fact[1]));
        facts.appendChild(item);
      });
      card.appendChild(facts);
      var verification = policyVerification(policy);
      card.appendChild(el("small", "insurance-past-source", verification ?
        "Verified against " + verification.source + " · " + dateLabel(verification.checkedAt) :
        "Historical record · Not insurer-verified"));
      list.appendChild(card);
    });
  }
  function renderInsurancePolicies() {
    var allPolicies = insurancePolicies();
    var policies = allPolicies.filter(function (entry) {
      return String(entry.policy.status || "In Force") === "In Force";
    });
    var pastPolicies = allPolicies.filter(function (entry) {
      return String(entry.policy.status || "In Force") !== "In Force";
    });
    var body = document.getElementById("insurance-policy-body");
    clear(body);
    policies.forEach(function (entry, index) {
      var policy = entry.policy;
      var status = String(policy.status || "In Force");
      var tr = el("tr", "insurance-policy-row" + (status === "In Force" ? "" : " inactive"));
      tr.tabIndex = 0;
      tr.setAttribute("role", "button");
      var summaryId = "insurance-policy-summary-" + index;
      tr.setAttribute("aria-expanded", "false");
      tr.setAttribute("aria-controls", summaryId);
      tr.setAttribute("aria-label", "Show a summary of " + policy.plan);
      var nameCell = el("td", "insurance-policy-name");
      nameCell.appendChild(el("strong", "", policy.plan));
      var sourceLine = el("span", "insurance-policy-source");
      var bundleComponents = insuranceBundleComponents(policy);
      var policyIdentity = entry.person.name + " · " + shortPolicyNumber(policy.policyNumber);
      if (policy.coverageOnly === true) {
        policyIdentity = entry.person.name + " · linked to " +
          (policy.premiumPaidBy || "another person") + "'s bundle";
      } else if (bundleComponents.length) {
        var insuredPeople = {};
        bundleComponents.forEach(function (component) {
          insuredPeople[component.insuredPerson] = true;
        });
        policyIdentity = Object.keys(insuredPeople).join(" + ") + " · " +
          bundleComponents.length + " active policies";
      }
      sourceLine.appendChild(document.createTextNode(policy.company + " · " + policyIdentity +
        (status === "In Force" ? "" : " · " + status)));
      if (policyVerification(policy)) {
        var verifiedBadge = el("span", "insurance-verified-badge",
          /MySinglife/i.test(policy.verification.source) ? "✓ MySinglife verified" : "✓ Verified");
        verifiedBadge.title = verificationTitle(policy);
        sourceLine.appendChild(verifiedBadge);
      }
      nameCell.appendChild(sourceLine);
      tr.appendChild(nameCell);
      var typeCell = el("td", "insurance-type-col");
      typeCell.appendChild(el("span", "insurance-type-pill", insuranceTypeLabel(policy.type)));
      tr.appendChild(typeCell);
      tr.appendChild(el("td", "insurance-benefit-summary", policyBenefitSummary(policy)));
      tr.appendChild(el("td", "insurance-money", policyCyclePremium(policy)));
      tr.appendChild(el("td", "insurance-money insurance-annual", policyAnnualPremiumLabel(policy)));
      tr.appendChild(el("td", "insurance-term", policy.payableTerm || "Not stated"));

      var detailRow = el("tr", "insurance-policy-summary-row hidden");
      detailRow.id = summaryId;
      var detailCell = document.createElement("td");
      detailCell.colSpan = 6;
      var detail = el("div", "insurance-policy-inline-summary");
      var copy = el("div", "insurance-policy-inline-copy");
      copy.appendChild(el("span", "insurance-policy-inline-label", "About this policy"));
      copy.appendChild(el("p", "", policyPlainSummary(policy)));
      var verification = policyVerification(policy);
      copy.appendChild(el("small", "", verification ?
        "Verified against " + verification.source + " · " + dateLabel(verification.checkedAt) :
        "Summary based on recorded policy details · Not insurer-verified"));
      var facts = el("div", "insurance-policy-inline-facts");
      insuranceInlineFacts(policy).forEach(function (fact) {
        var item = el("span", "insurance-policy-inline-fact");
        item.appendChild(el("small", "", fact[0]));
        item.appendChild(el("strong", "", fact[1]));
        facts.appendChild(item);
      });
      copy.appendChild(facts);
      addInsuranceBundleBreakdown(copy, policy);
      detail.appendChild(copy);
      var fullButton = el("button", "insurance-policy-full-button", "Full policy record");
      fullButton.type = "button";
      fullButton.addEventListener("click", function (event) {
        event.stopPropagation();
        openInsuranceDrawer(policy, entry.person);
      });
      detail.appendChild(fullButton);
      detailCell.appendChild(detail);
      detailRow.appendChild(detailCell);

      function toggle() {
        var isOpen = tr.getAttribute("aria-expanded") === "true";
        tr.setAttribute("aria-expanded", isOpen ? "false" : "true");
        tr.setAttribute("aria-label", (isOpen ? "Show" : "Hide") +
          " the summary of " + policy.plan);
        detailRow.classList.toggle("hidden", isOpen);
      }
      tr.addEventListener("click", toggle);
      tr.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); }
      });
      body.appendChild(tr);
      body.appendChild(detailRow);
    });
    var linkedPolicyCount = policies.filter(function (entry) {
      return entry.policy.coverageOnly === true;
    }).length;
    document.getElementById("insurance-policy-count").textContent =
      (policies.length - linkedPolicyCount) + " active entries" +
      (linkedPolicyCount ? " + " + linkedPolicyCount + " linked cover" : "");
    var pastButton = document.getElementById("insurance-past-button");
    pastButton.textContent = "Past policies (" + pastPolicies.length + ")";
    pastButton.classList.toggle("hidden", !pastPolicies.length);
    renderPastInsurancePolicies(pastPolicies);
  }
  function insuranceChargeGroups(result) {
    var selectedYear = String(state.insuranceYear || "");
    var yearStart = selectedYear + "-01-01";
    var yearEnd = selectedYear + "-12-31";
    function annualDueDate(policy) {
      var startDate = String(policy.startDate || "");
      if (!startDate) return "";
      var month = parseInt(startDate.slice(5, 7), 10);
      var requestedDay = parseInt(startDate.slice(8, 10), 10);
      var lastDay = new Date(parseInt(selectedYear, 10), month, 0).getDate();
      var day = Math.min(requestedDay, lastDay);
      var dueDate = selectedYear + "-" + String(month).padStart(2, "0") + "-" +
        String(day).padStart(2, "0");
      if (dueDate < startDate || (policy.premiumEndDate && dueDate > policy.premiumEndDate)) return "";
      return dueDate;
    }
    var statementHistory = insurancePolicyStatementHistory(result.policies);
    var groups = result.policies.filter(function (entry) {
      var policy = entry.policy;
      var frequency = String(policy.premiums && policy.premiums.frequency || "");
      return String(policy.status || "In Force") === "In Force" &&
        policyPaymentAmount(policy) > 0 && (frequency === "Monthly" || frequency === "Annual") &&
        (!policy.startDate || policy.startDate <= yearEnd) &&
        (!policy.premiumEndDate || policy.premiumEndDate >= yearStart);
    }).map(function (entry) {
      var frequency = String(entry.policy.premiums && entry.policy.premiums.frequency || "");
      return {
        key: "policy:" + entry.policy.id,
        label: entry.policy.plan,
        detail: entry.policy.company + " · Matched policy",
        policy: entry.policy,
        person: entry.person,
        scheduledCount: entry.scheduledCount,
        matchedCount: entry.matchedCount,
        cutoff: result.cutoff,
        annualDueDate: frequency === "Annual" ? annualDueDate(entry.policy) : "",
        paymentHistory: statementHistory[entry.policy.id] || [],
        lastPayment: (statementHistory[entry.policy.id] || [])[0] || null,
        transactions: entry.transactions.slice()
      };
    });
    var unlinked = {};
    result.unmatched.concat(result.refunds).forEach(function (transaction) {
      var key = transactionName(transaction) || transaction.description;
      if (!unlinked[key]) {
        unlinked[key] = {
          key: "unlinked:" + key,
          label: key,
          detail: "Not linked to a policy",
          policy: null,
          person: null,
          transactions: []
        };
      }
      unlinked[key].transactions.push(transaction);
    });
    Object.keys(unlinked).forEach(function (key) { groups.push(unlinked[key]); });
    groups.forEach(function (group) {
      group.transactions.sort(function (a, b) {
        return (b.date || b.month).localeCompare(a.date || a.month);
      });
      group.latest = group.transactions.length
        ? group.transactions[0].date || group.transactions[0].month + "-01" : "";
      group.total = insuranceNet(group.transactions);
    });
    return groups.sort(function (a, b) {
      var aDue = a.scheduledCount > 0 ? 0 : 1;
      var bDue = b.scheduledCount > 0 ? 0 : 1;
      if (aDue !== bDue) return aDue - bDue;
      if (!aDue) return String(b.latest || "").localeCompare(String(a.latest || ""));
      return String(a.annualDueDate || "").localeCompare(String(b.annualDueDate || ""));
    });
  }
  var INSURANCE_MONTH_LABELS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function appendInsuranceLastPayment(parent, group) {
    var evidence = group.lastPayment;
    if (evidence && evidence.transaction) {
      var transaction = evidence.transaction;
      var previous = el("span", "insurance-last-payment" +
        (evidence.amountChanged ? " changed" : ""));
      previous.textContent = "Last statement charge · " + fmt(transaction.amount) + " on " +
        dateLabel(transaction.date || transaction.month + "-01") +
        (evidence.amountChanged ? " · amount differs" : "");
      previous.title = "Matched from the card statement by " + evidence.matchType + ".";
      parent.appendChild(previous);
    } else {
      parent.appendChild(el("span", "insurance-last-payment unavailable",
        "No earlier matching statement charge found"));
    }
  }
  function appendInsurancePaymentHistory(parent, group) {
    var history = group.paymentHistory || [];
    var wrap = el("span", "insurance-payment-history");
    var start = group.policy && group.policy.startDate;
    wrap.appendChild(el("span", "", start ? "Policy started · " + dateLabel(start) :
      "Policy start date not recorded"));
    if (history.length) {
      var total = roundMoney(history.reduce(function (sum, evidence) {
        return sum + Number(evidence.transaction && evidence.transaction.amount || 0);
      }, 0));
      var earliest = history[history.length - 1].transaction;
      var earliestDate = earliest.date || earliest.month + "-01";
      wrap.appendChild(el("strong", "", "Statement-confirmed total · " + fmt(total) +
        " across " + history.length + " payment" + (history.length === 1 ? "" : "s") +
        " since " + dateLabel(earliestDate)));
    } else {
      wrap.appendChild(el("span", "insurance-payment-history-empty",
        "No uniquely matched payment in imported statements"));
    }
    parent.appendChild(wrap);
  }
  function appendMonthlyInsuranceTimeline(parent, group) {
    var policy = group.policy;
    var year = parseInt(state.insuranceYear, 10);
    var cutoff = group.cutoff;
    var startDate = String(policy.startDate || "");
    var premiumEndDate = String(policy.premiumEndDate || "");
    var dueDay = parseInt(startDate.slice(8, 10), 10) || 1;
    var paidMonths = {};
    group.transactions.forEach(function (transaction) {
      var date = String(transaction.date || transaction.month || "");
      if (date.slice(0, 4) === String(year) && transaction.type === "debit") {
        var month = parseInt(date.slice(5, 7), 10);
        paidMonths[month] = (paidMonths[month] || 0) + 1;
      }
    });

    var track = el("span", "insurance-month-track");
    var accessibleStates = [];
    INSURANCE_MONTH_LABELS.forEach(function (label, index) {
      var month = index + 1;
      var dueDate = String(year) + "-" + String(month).padStart(2, "0") + "-" +
        String(dueDay).padStart(2, "0");
      var stateName = "upcoming";
      if ((startDate && dueDate < startDate) || (premiumEndDate && dueDate > premiumEndDate)) {
        stateName = "inactive";
      } else if (paidMonths[month]) {
        stateName = "paid";
      } else if (dueDate <= cutoff) {
        stateName = "missing";
      }
      var stateLabel = {
        paid: "paid", missing: "payment missing", upcoming: "upcoming", inactive: "not applicable"
      }[stateName];
      var cell = el("span", "insurance-month-cell " + stateName, label);
      cell.title = label + ": " + stateLabel;
      cell.setAttribute("aria-hidden", "true");
      track.appendChild(cell);
      accessibleStates.push(label + " " + stateLabel);
    });
    track.setAttribute("aria-label", "Monthly premium status for " + year + ": " +
      accessibleStates.join(", "));
    track.setAttribute("role", "img");
    parent.appendChild(track);
    var resultText = group.scheduledCount
      ? group.matchedCount + " of " + group.scheduledCount + " due paid"
      : "No instalment due yet";
    parent.appendChild(el("span", "insurance-cadence-result" +
      (group.matchedCount < group.scheduledCount ? " missing" : ""), resultText));
    appendInsuranceLastPayment(parent, group);
  }
  function appendAnnualInsuranceTimeline(parent, group) {
    var year = String(state.insuranceYear || "");
    var dueDate = group.annualDueDate || group.latest;
    var dueMonth = dueDate ? parseInt(dueDate.slice(5, 7), 10) : 0;
    var paymentState = group.transactions.length ? "paid" :
      dueDate && dueDate <= group.cutoff ? "missing" : "annual-due";
    var stateLabel = paymentState === "paid" ? "paid" :
      paymentState === "missing" ? "payment missing" : "upcoming payment";
    var track = el("span", "insurance-month-track annual");
    INSURANCE_MONTH_LABELS.forEach(function (label, index) {
      var isPaymentMonth = index + 1 === dueMonth;
      var cell = el("span", "insurance-month-cell " +
        (isPaymentMonth ? paymentState : "annual-off"), label);
      cell.title = label + (isPaymentMonth ? ": " + stateLabel : ": no annual premium due");
      cell.setAttribute("aria-hidden", "true");
      track.appendChild(cell);
    });
    track.setAttribute("aria-label", "Annual premium status for " + year + ": " +
      (dueMonth ? INSURANCE_MONTH_LABELS[dueMonth - 1] + " " + stateLabel : "payment month unknown"));
    track.setAttribute("role", "img");
    parent.appendChild(track);
    var statusDate = group.transactions.length ? group.latest : dueDate;
    var resultText = group.transactions.length
      ? "Paid once yearly · " + dateLabel(statusDate)
      : paymentState === "annual-due"
        ? "Next payment · " + dateLabel(statusDate)
        : "Payment overdue · " + dateLabel(statusDate);
    parent.appendChild(el("span", "insurance-cadence-result annual-result" +
      (paymentState === "missing" ? " missing" : paymentState === "annual-due" ? " upcoming" : ""),
    resultText));
    appendInsuranceLastPayment(parent, group);
  }
  function appendInsuranceChargeGroup(body, group, index) {
    var id = "insurance-charge-group-" + index;
    var hasEntries = group.transactions.length > 0;
    var row = el("tr", "insurance-charge-group-row" + (group.policy ? " matched" : " unlinked"));
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    if (hasEntries) {
      row.setAttribute("aria-expanded", "false");
      row.setAttribute("aria-controls", id);
      row.setAttribute("aria-label", "Show statement entries for " + group.label);
    } else {
      row.classList.add("no-entries");
      row.setAttribute("aria-label", "View policy details for " + group.label);
    }
    var policyCell = el("td", "insurance-charge-policy");
    policyCell.appendChild(el("span", "insurance-charge-expand", "›"));
    var policyCopy = el("span", "insurance-charge-policy-copy");
    policyCopy.appendChild(el("strong", "", group.label));
    var meta = el("span", "insurance-charge-meta");
    if (group.policy) {
      var frequency = String(group.policy.premiums && group.policy.premiums.frequency || "");
      var paymentState = group.scheduledCount === 0 && group.annualDueDate > group.cutoff
        ? "upcoming" : group.matchedCount >= group.scheduledCount && group.scheduledCount > 0
          ? "matched" : "missing";
      meta.appendChild(el("small", "insurance-charge-cadence", frequency || "Unscheduled"));
      meta.appendChild(el("small", paymentState,
        paymentState === "matched" ? "Matched" : paymentState === "upcoming" ? "Upcoming" : "Check payment"));
      if (policyVerification(group.policy)) {
        var verificationBadge = el("small", "verified-source", "✓ Verified");
        verificationBadge.title = verificationTitle(group.policy);
        meta.appendChild(verificationBadge);
      }
    } else {
      meta.appendChild(el("small", "unlinked", group.detail));
    }
    policyCopy.appendChild(meta);
    if (group.policy && String(group.policy.premiums && group.policy.premiums.frequency) === "Monthly") {
      appendMonthlyInsuranceTimeline(policyCopy, group);
    } else if (group.policy && String(group.policy.premiums && group.policy.premiums.frequency) === "Annual") {
      appendAnnualInsuranceTimeline(policyCopy, group);
    }
    if (group.policy) appendInsurancePaymentHistory(policyCopy, group);
    policyCell.appendChild(policyCopy);
    row.appendChild(policyCell);
    row.appendChild(el("td", "insurance-charge-count", String(group.transactions.length)));
    row.appendChild(el("td", "insurance-charge-latest", group.latest
      ? shortDate(group.latest, false)
      : group.annualDueDate ? shortDate(group.annualDueDate, true) : "—"));
    var pendingAnnual = group.policy && !group.transactions.length && group.annualDueDate;
    var amountClass = "col-amt" + (group.total < 0 ? " credit" : "") +
      (pendingAnnual && group.annualDueDate > group.cutoff
        ? " insurance-upcoming-amount" : pendingAnnual ? " insurance-missing-amount" : "");
    row.appendChild(el("td", amountClass, group.transactions.length
      ? fmt(group.total) : pendingAnnual ? fmt(policyPaymentAmount(group.policy)) : "—"));

    var detailRow = el("tr", "insurance-charge-detail-row hidden");
    detailRow.id = id;
    var detailCell = document.createElement("td");
    detailCell.colSpan = 4;
    var detail = el("div", "insurance-charge-detail");
    var head = el("div", "insurance-charge-detail-head");
    head.appendChild(el("span", "", group.transactions.length + " statement entr" +
      (group.transactions.length === 1 ? "y" : "ies")));
    if (group.policy) {
      var policyButton = el("button", "insurance-policy-link", "View policy details");
      policyButton.addEventListener("click", function (event) {
        event.stopPropagation();
        openInsuranceDrawer(group.policy, group.person);
      });
      head.appendChild(policyButton);
    }
    detail.appendChild(head);
    var list = el("div", "insurance-charge-items");
    group.transactions.forEach(function (transaction) {
      var item = el("button", "insurance-charge-item");
      item.type = "button";
      item.appendChild(el("span", "insurance-charge-item-date",
        transaction.date ? shortDate(transaction.date, false) : monthLabel(transaction.month)));
      item.appendChild(el("span", "insurance-charge-item-description", transactionName(transaction)));
      var credit = transaction.type !== "debit";
      item.appendChild(el("strong", credit ? "credit" : "",
        (credit ? "+" : "−") + fmt(transaction.amount)));
      item.addEventListener("click", function (event) {
        event.stopPropagation();
        openTransactionDrawer(transaction);
      });
      list.appendChild(item);
    });
    detail.appendChild(list);
    detailCell.appendChild(detail);
    detailRow.appendChild(detailCell);

    function toggle() {
      if (!hasEntries && group.policy) {
        openInsuranceDrawer(group.policy, group.person);
        return;
      }
      var open = row.getAttribute("aria-expanded") === "true";
      row.setAttribute("aria-expanded", open ? "false" : "true");
      detailRow.classList.toggle("hidden", open);
    }
    row.addEventListener("click", toggle);
    row.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
    body.appendChild(row);
    if (hasEntries) body.appendChild(detailRow);
  }
  function renderInsuranceCharges() {
    var all = data.transactions.filter(function (t) { return t.category === "Insurance"; });
    var years = {};
    all.forEach(function (t) { years[(t.date || t.month).slice(0, 4)] = true; });
    var yearList = Object.keys(years).sort();
    if (!state.insuranceYear || yearList.indexOf(state.insuranceYear) === -1) {
      state.insuranceYear = yearList.length ? yearList[yearList.length - 1] : null;
    }
    var pills = document.getElementById("insurance-years");
    clear(pills);
    yearList.forEach(function (year) {
      var button = el("button", "pill" + (year === state.insuranceYear ? " active" : ""), year);
      setPressed(button, year === state.insuranceYear);
      button.addEventListener("click", function () { state.insuranceYear = year; renderInsurance(); });
      pills.appendChild(button);
    });
    var rows = insuranceChargeRows().sort(function (a, b) {
      return (b.date || b.month).localeCompare(a.date || a.month);
    });
    var reconciliation = reconcileInsurance(rows);
    var groups = insuranceChargeGroups(reconciliation);
    var body = document.getElementById("insurance-charge-body");
    clear(body);
    groups.forEach(function (group, index) {
      appendInsuranceChargeGroup(body, group, index);
    });
    if (!groups.length) {
      var emptyRow = document.createElement("tr");
      var emptyCell = el("td", "empty", "No matching insurance charges in this year");
      emptyCell.colSpan = 4;
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
    }
    document.getElementById("insurance-charge-note").textContent =
      "Monthly calendars mark every paid instalment. Annual calendars highlight only the single payment month: green when paid, blue when upcoming and red when overdue. MediSave/CPF premiums are excluded from card reconciliation.";
    document.getElementById("insurance-charge-note").textContent +=
      " Policies paid from external accounts are also excluded. Future annual dates use each policy's premium anniversary. Previous-payment lines come from matched card statements.";
    renderInsuranceReconciliation(rows, reconciliation);
    var foot = document.getElementById("insurance-charge-foot");
    clear(foot);
    foot.appendChild(el("span", "", groups.length + " polic" +
      (groups.length === 1 ? "y" : "ies") + " shown · " + rows.length + " posted charge" +
      (rows.length === 1 ? "" : "s")));
    foot.appendChild(el("strong", "", "Net " + fmt(insuranceNet(rows))));
  }
  function renderInsurance() {
    if (!data.insurance) return;
    var people = insurancePeople();
    if (state.insurancePerson !== "all" && !people.some(function (person) {
      return person.id === state.insurancePerson;
    })) state.insurancePerson = "all";

    var selected = selectedInsurancePeople();
    var currentPerson = selected.length === 1 ? selected[0] : null;
    document.getElementById("insurance-portfolio-title").textContent = currentPerson
      ? currentPerson.name + "'s insurance" : "Combined insurance";
    var updated = data.insurance.extractedAt
      ? "Updated " + dateLabel(data.insurance.extractedAt.slice(0, 10)) : "Source date not recorded";
    var portfolioTotals = currentPerson ? currentPerson.totals : data.insurance.totals;
    var visiblePolicyEntries = insurancePolicies();
    var linkedCoverageCount = visiblePolicyEntries.filter(function (entry) {
      return entry.policy.coverageOnly === true;
    }).length;
    var verifiedCount = visiblePolicyEntries.filter(function (entry) {
      return Boolean(policyVerification(entry.policy));
    }).length;
    document.getElementById("insurance-source-meta").textContent =
      Number(portfolioTotals.activePolicies || 0) + " active records · " +
      Number(portfolioTotals.policies || 0) + " total records" +
      (linkedCoverageCount ? " · " + linkedCoverageCount + " linked cover" : "") + " · " + updated +
      (verifiedCount ? " · " + verifiedCount + " insurer-verified" : "") +
      " · premiums annualised for comparison";

    var personPills = document.getElementById("insurance-person-pills");
    clear(personPills);
    [{ id: "all", name: "All" }].concat(people).forEach(function (person) {
      var active = person.id === state.insurancePerson;
      var button = el("button", "pill" + (active ? " active" : ""), person.name);
      setPressed(button, active);
      button.addEventListener("click", function () {
        state.insurancePerson = person.id;
        renderInsurance();
      });
      personPills.appendChild(button);
    });

    var totals = insuranceTotals();
    var kpis = document.getElementById("insurance-kpis");
    clear(kpis);
    var annualCash = Number(totals.annualCashPremium || 0);
    var annualCpf = Number(totals.annualCpfPremium || 0);
    var annualTotal = roundMoney(annualCash + annualCpf);
    var cashShare = annualTotal ? Math.round(annualCash / annualTotal * 100) : 0;
    var cpfShare = annualTotal ? 100 - cashShare : 0;
    var cashCard = metric("Cash premiums", fmt(annualCash),
      fmt(roundMoney(annualCash / 12)) + " monthly equivalent", null, "wallet");
    cashCard.classList.add("insurance-kpi", "primary");
    kpis.appendChild(cashCard);
    var cpfCard = metric("CPF / MediSave", fmt(annualCpf),
      fmt(roundMoney(annualCpf / 12)) + " monthly equivalent", null, "shield");
    cpfCard.classList.add("insurance-kpi");
    kpis.appendChild(cpfCard);
    var totalCard = metric("Total premiums", fmt(annualTotal),
      cashShare + "% cash · " + cpfShare + "% CPF", null, "calendar");
    totalCard.classList.add("insurance-kpi");
    kpis.appendChild(totalCard);
    var policyCard = metric("Portfolio entries",
      String(Number(totals.policies || 0) + linkedCoverageCount),
      String(totals.activePolicies || 0) + " active entries" +
      (linkedCoverageCount ? " · " + linkedCoverageCount + " paid by Nick" : "") +
      (totals.annualCpfPremium ? " · " + fmt(totals.annualCpfPremium) + " annual CPF" : ""),
      null, "list");
    policyCard.classList.add("insurance-kpi");
    kpis.appendChild(policyCard);

    renderInsuranceCoverage();
    renderInsurancePolicies();
    renderInsuranceCharges();
  }

  // ---------- Games ----------

  function gameTx() {
    return data.transactions.filter(function (t) { return t.category === "Games"; });
  }

  function renderGames() {
    var all = gameTx();
    var years = {};
    all.forEach(function (t) { years[t.month.slice(0, 4)] = true; });
    (data.gameSales || []).forEach(function (s) { years[s.month.slice(0, 4)] = true; });
    var yearList = Object.keys(years).sort();
    if (!yearList.length) return;
    if (state.gameYear !== "All" && yearList.indexOf(state.gameYear) === -1) {
      state.gameYear = yearList[yearList.length - 1];
    }

    var yearWrap = document.getElementById("games-years");
    clear(yearWrap);
    ["All"].concat(yearList).forEach(function (y) {
      var b = el("button", "pill" + (y === state.gameYear ? " active" : ""),
        y === "All" ? "All years" : y);
      setPressed(b, y === state.gameYear);
      b.addEventListener("click", function () { state.gameYear = y; renderGames(); });
      yearWrap.appendChild(b);
    });

    var inYear = all.filter(function (t) {
      return state.gameYear === "All" || t.month.slice(0, 4) === state.gameYear;
    });
    var byGame = {};
    inYear.forEach(function (t) {
      var g = t.game || "Other games";
      byGame[g] = (byGame[g] || 0) + signed(t);
    });
    var gameNames = Object.keys(byGame).sort(function (a, b) { return byGame[b] - byGame[a]; });
    if (state.game !== "All" && gameNames.indexOf(state.game) === -1) state.game = "All";

    var pills = document.getElementById("game-pills");
    clear(pills);
    ["All"].concat(gameNames).forEach(function (g) {
      var b = el("button", "pill" + (g === state.game ? " active" : ""),
        g === "All" ? "All games" : g);
      setPressed(b, g === state.game);
      b.addEventListener("click", function () { state.game = g; renderGames(); });
      pills.appendChild(b);
    });

    var rows = inYear.filter(function (t) {
      return state.game === "All" || (t.game || "Other games") === state.game;
    });
    // Sales carry the same filters as spending, or Net would subtract one game's
    // spending from every game's sales. Computed after state.game is validated.
    var sales = (data.gameSales || []).filter(function (s) {
      if (state.gameYear !== "All" && s.month.slice(0, 4) !== state.gameYear) return false;
      if (state.game !== "All" && (s.publisher || s.game) !== state.game) return false;
      return true;
    });

    var total = 0;
    rows.forEach(function (t) { total += signed(t); });
    var months = {};
    rows.forEach(function (t) { months[t.month] = (months[t.month] || 0) + signed(t); });
    var monthKeys = Object.keys(months).sort();
    var spanMonths = monthKeys.length || 1;
    var top = gameNames[0];

    var earned = 0;
    sales.forEach(function (s) { earned += s.amount; });
    var net = earned - total;

    var kpis = document.getElementById("games-kpis");
    clear(kpis);
    kpis.appendChild(metric(state.gameYear === "All" ? "Spent on games" : "Spent in " + state.gameYear,
      fmt0(total), rows.length + " charges", null, "gamepad"));
    kpis.appendChild(metric("Earned from sales", fmt0(earned),
      sales.length + " account" + (sales.length === 1 ? "" : "s") + " sold", null, "coins"));
    kpis.appendChild(metric("Net", (net >= 0 ? "+" : "") + fmt0(net),
      net >= 0 ? "ahead overall" : "down overall", net >= 0 ? "good" : "bad", "up"));
    kpis.appendChild(metric("Per month", fmt0(total / spanMonths),
      "across " + spanMonths + " active month" + (spanMonths === 1 ? "" : "s"), null, "calendar"));

    var salesWrap = document.getElementById("sales-list");
    clear(salesWrap);
    document.getElementById("sales-hint").textContent = earned > 0
      ? fmt0(earned) + " across " + sales.length + " sale" + (sales.length === 1 ? "" : "s")
      : "";
    if (!sales.length) {
      salesWrap.appendChild(emptyState(
        "No account sales in this period",
        "Select another year or All to see recorded game-account sales.",
        "coins"
      ));
    } else {
      var maxSale = Math.max.apply(null, sales.map(function (s) { return s.amount; }));
      sales.slice().sort(function (a, b) { return b.month.localeCompare(a.month); })
        .forEach(function (s) {
          var row = el("div", "cat-row");
          row.appendChild(el("span", "name", s.game));
          var track = el("div", "track");
          var fill = el("div", "fill");
          fill.style.width = Math.max(3, Math.round((s.amount / maxSale) * 100)) + "%";
          fill.style.background = "var(--green)";
          track.appendChild(fill);
          row.appendChild(track);
          var amt = el("span", "amt", fmt0(s.amount));
          amt.title = monthLabel(s.month);
          row.appendChild(amt);
          row.appendChild(el("span", "sale-when", monthLabel(s.month)));
          salesWrap.appendChild(row);
        });
    }

    var breakdown = document.getElementById("games-breakdown");
    clear(breakdown);
    var shades = ["var(--bar-1)", "var(--bar-2)", "var(--bar-3)", "var(--bar-4)"];
    var max = gameNames.length ? Math.max(byGame[gameNames[0]], 1) : 1;
    gameNames.forEach(function (name, i) {
      var row = el("div", "cat-row");
      row.appendChild(el("span", "name", name));
      var track = el("div", "track");
      var fill = el("div", "fill");
      fill.style.width = Math.max(1, Math.round((Math.max(byGame[name], 0) / max) * 100)) + "%";
      fill.style.background = name === state.game || state.game === "All"
        ? shades[Math.min(i, shades.length - 1)] : "var(--border)";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "amt", fmt(byGame[name])));
      breakdown.appendChild(row);
    });
    if (!gameNames.length) breakdown.appendChild(emptyState(
      "No game spending recorded",
      "There are no game charges in the selected period.",
      "gamepad"
    ));

    var trend = document.getElementById("games-trend");
    clear(trend);
    document.getElementById("games-trend-hint").textContent =
      state.game === "All" ? "all games" : state.game;
    var maxMonth = 1;
    monthKeys.forEach(function (m) { maxMonth = Math.max(maxMonth, months[m]); });
    monthKeys.slice().reverse().forEach(function (m) {
      var row = el("div", "cat-row");
      row.appendChild(el("span", "name", monthLabel(m)));
      var track = el("div", "track");
      var fill = el("div", "fill");
      fill.style.width = Math.max(1, Math.round((Math.max(months[m], 0) / maxMonth) * 100)) + "%";
      fill.style.background = "var(--bar-2)";
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("span", "amt", fmt(months[m])));
      trend.appendChild(row);
    });
    if (!monthKeys.length) trend.appendChild(emptyState(
      "No monthly trend yet",
      "A trend appears after the first matching game charge.",
      "bars"
    ));

    var body = document.getElementById("games-body");
    clear(body);
    rows.slice().sort(function (a, b) {
      return (b.date || b.month).localeCompare(a.date || a.month);
    }).forEach(function (t) {
      var tr = document.createElement("tr");
      var d = t.date
        ? t.date.slice(8, 10) + " " + MONTH_NAMES[parseInt(t.date.slice(5, 7), 10) - 1] + " " + t.date.slice(2, 4)
        : t.month;
      tr.appendChild(el("td", "col-date", d));
      var tdDesc = el("td", "", t.description);
      tdDesc.title = t.description;
      tr.appendChild(tdDesc);
      var tdGame = el("td", "col-cat");
      tdGame.appendChild(el("span", "cat-pill cat-games", t.game || "Other games"));
      tr.appendChild(tdGame);
      var credit = t.type !== "debit";
      tr.appendChild(el("td", "col-amt" + (credit ? " credit" : ""),
        (credit ? "+" : "-") + t.amount.toFixed(2)));
      appendExpandableRow(body, tr, t, 4, [
        ["Date", t.date || statementLabel(t.month)],
        ["Posted", t.postedDate || t.date || statementLabel(t.month)],
        ["Statement", statementLabel(t.month)],
        ["Game", t.game || "Other games"],
        ["Card", t.card || "UOB ONE CARD"],
        ["Amount", (credit ? "+" : "-") + fmt(t.amount)]
      ], "games");
    });
    document.getElementById("games-count").textContent =
      rows.length + " charge" + (rows.length === 1 ? "" : "s") +
      (state.game === "All" ? "" : " · " + state.game);
  }

  // ---------- Split ----------

  // Both the Split tab and the Overview insight read this one calculation, so
  // the two "You owe Yx" figures can only differ by their stated time scope.
  function settlementFor(year, throughMonth) {
    return window.FinanceGrouping.settlementPosition(
      data.transactions, data.settlements, year, throughMonth || null,
      { excludedCategories: EXCLUDED });
  }

  function settlementPosition(amount) {
    return amount >= 0
      ? "Yx owes you " + fmt(amount)
      : "You owe Yx " + fmt(Math.abs(amount));
  }

  function auditRow(operator, label, detail, amount, tone, total) {
    var row = el("div", "settlement-audit-row" + (total ? " total" : ""));
    row.appendChild(el("span", "settlement-operator", operator));
    var copy = el("div", "settlement-audit-copy");
    copy.appendChild(el("strong", "", label));
    if (detail) copy.appendChild(el("span", "", detail));
    row.appendChild(copy);
    row.appendChild(el("span", "settlement-audit-amount" + (tone ? " " + tone : ""), amount));
    return row;
  }

  function renderSplit() {
    var years = {};
    data.transactions.forEach(function (t) { years[t.month.slice(0, 4)] = true; });
    var list = Object.keys(years).sort();
    if (!state.splitYear) state.splitYear = list[list.length - 1];

    var pills = document.getElementById("split-years");
    clear(pills);
    list.forEach(function (y) {
      var b = el("button", "pill" + (y === state.splitYear ? " active" : ""), y);
      setPressed(b, y === state.splitYear);
      b.addEventListener("click", function () { state.splitYear = y; renderSplit(); });
      pills.appendChild(b);
    });

    // Positive netPosition means Yx owes Nic; a positive opening payable moves
    // the position in the opposite direction. The Split tab covers the whole
    // selected year, so it passes no month cutoff.
    var position = settlementFor(state.splitYear, null);
    var rows = position.byMonth;
    var totals = position.ownerTotals;
    var yearMonths = position.months;
    var sharedHalf = position.sharedHalf;
    var newYxShare = position.newYxShare;
    var opening = position.hasOpening;
    var openingYouOwe = position.openingYouOwe;
    var paidToYx = position.paidToYx;
    var receivedFromYx = position.receivedFromYx;
    var netPosition = position.netPosition;
    var summary = document.getElementById("split-summary");
    clear(summary);
    var grid = el("div", "kpis");
    grid.appendChild(metric(
      opening ? "Current settlement position · " + position.scopeLabel
        : "Yx owes you for " + state.splitYear,
      fmt(Math.abs(netPosition)),
      opening
        ? settlementPosition(netPosition) + " · " + position.scopeLabel
        : "confirmed tags: half of shared" +
          (totals.Yx > 0 ? " plus her direct charges" : "") +
          " · " + position.scopeLabel,
      netPosition >= 0 ? "good" : "bad",
      "coins"
    ));
    grid.appendChild(metric("New Yx share", fmt(newYxShare),
      fmt(sharedHalf) + " shared half + " + fmt(totals.Yx) + " direct", null, "users"));
    grid.appendChild(metric("Shared spending", fmt(totals.Shared), "split 50/50", null, "users"));
    grid.appendChild(metric("Unassigned", fmt(totals.Untagged),
      totals.Untagged ? "review before settling up" : "nothing unresolved",
      totals.Untagged ? "bad" : "good", "receipt"));
    summary.appendChild(grid);
    if (Math.abs(totals.Untagged) >= 0.01) {
      var pct = Math.abs(totals.Untagged) /
        Math.max(Math.abs(totals.Nic + totals.Shared + totals.Yx + totals.Untagged), 0.01) * 100;
      var warning = el("div", "audit-warning");
      var warningText = el("span", "");
      warningText.appendChild(el("strong", "", "Audit incomplete. "));
      warningText.appendChild(document.createTextNode(
        fmt(totals.Untagged) + " (" + pct.toFixed(1) +
        "% of spending) has no owner and is excluded from the amount Yx owes."));
      warning.appendChild(warningText);
      var review = el("button", "link-button", "Review unassigned");
      review.addEventListener("click", function () {
        // Route through openTransactions like every other drill-down: the
        // hand-rolled version left a bank source or a stale insight id-filter
        // in place and showed the wrong rows entirely.
        openTransactions({
          owner: "Untagged",
          period: { mode: "year", year: state.splitYear, month: null }
        });
      });
      warning.appendChild(review);
      summary.appendChild(warning);
    }

    var audit = document.getElementById("settlement-audit");
    clear(audit);
    var auditHead = el("div", "settlement-audit-head");
    var auditTitle = el("div", "");
    auditTitle.appendChild(el("strong", "", "How this balance is calculated"));
    auditTitle.appendChild(el("span", "", "Positive amounts below reduce what you owe."));
    auditHead.appendChild(auditTitle);
    auditHead.appendChild(el("span", "audit-check", "Balances to the cent"));
    audit.appendChild(auditHead);
    audit.appendChild(auditRow(
      "", "Opening position",
      opening
        ? (position.carried
            ? "Carried forward from the " + position.carriedFromYear +
              " closing balance, recorded from " + dateLabel(position.openingFrom + "-01")
            : "Recorded from " + dateLabel(position.openingFrom + "-01"))
        : "No opening balance recorded, so no running position is carried",
      fmt(openingYouOwe), "settlement-payable", false
    ));
    audit.appendChild(auditRow(
      "−", "Your credit for shared spending",
      // Each month's half is rounded before summing (cent-level settling), so
      // this is not always exactly 50% of the shared total; the label must not
      // claim otherwise.
      "Half of each month's share of " + fmt(totals.Shared),
      fmt(sharedHalf), "", false
    ));
    audit.appendChild(auditRow(
      "−", "YX direct charges",
      "Charges assigned fully to YX",
      fmt(totals.Yx), "", false
    ));
    audit.appendChild(auditRow(
      "−", "Payments you made to YX",
      position.paidToYxCount + " recorded payment" +
        (position.paidToYxCount === 1 ? "" : "s"),
      fmt(paidToYx), "", false
    ));
    audit.appendChild(auditRow(
      "+", "Payments received from YX",
      position.receivedFromYxCount + " recorded payment" +
        (position.receivedFromYxCount === 1 ? "" : "s"),
      fmt(receivedFromYx), "", false
    ));
    audit.appendChild(auditRow(
      "=", settlementPosition(netPosition) + " · " + position.scopeLabel,
      "Opening − credits − payments to YX + payments from YX",
      fmt(Math.abs(netPosition)),
      netPosition >= 0 ? "settlement-receivable" : "settlement-payable",
      true
    ));

    var wrap = document.getElementById("split-table");
    clear(wrap);
    document.getElementById("settlement-evidence-hint").textContent =
      yearMonths.length + " statement month" + (yearMonths.length === 1 ? "" : "s") +
      " · " + (totals.Untagged ? fmt(totals.Untagged) + " unassigned" : "all owners assigned");
    var table = el("table", "mini wide");
    var head = document.createElement("tr");
    ["Month", "Yours", "Shared", "Yx direct", "Unassigned", "Settlement"].forEach(function (h, i) {
      head.appendChild(el("th", i ? "num" : "", h));
    });
    table.appendChild(head);
    if (opening) {
      var openingRow = document.createElement("tr");
      openingRow.className = "settlement-opening-row";
      openingRow.appendChild(el("td", "", position.carried
        ? "Opening balance (carried from " + position.carriedFromYear + ")"
        : "Opening balance"));
      openingRow.appendChild(el("td", "num", "—"));
      openingRow.appendChild(el("td", "num", "—"));
      openingRow.appendChild(el("td", "num", "—"));
      openingRow.appendChild(el("td", "num", "—"));
      openingRow.appendChild(el("td", "num strong settlement-payable",
        "You owe Yx " + fmt(openingYouOwe)));
      table.appendChild(openingRow);
    }
    yearMonths.forEach(function (m) {
      var r = rows[m];
      var tr = document.createElement("tr");
      tr.appendChild(el("td", "", monthLabel(m)));
      tr.appendChild(el("td", "num", fmt(r.Nic)));
      tr.appendChild(el("td", "num", fmt(r.Shared)));
      tr.appendChild(el("td", "num", r.Yx ? fmt(r.Yx) : "—"));
      tr.appendChild(el("td", "num", r.Untagged ? fmt(r.Untagged) : "—"));
      tr.appendChild(el("td", "num strong", fmt(r.yxShare)));
      table.appendChild(tr);
    });
    var foot = document.createElement("tr");
    foot.className = "total-row";
    foot.appendChild(el("td", "",
      (opening ? "Current position" : "Total") + " · " + position.scopeLabel));
    foot.appendChild(el("td", "num", fmt(totals.Nic)));
    foot.appendChild(el("td", "num", fmt(totals.Shared)));
    foot.appendChild(el("td", "num", totals.Yx ? fmt(totals.Yx) : "—"));
    foot.appendChild(el("td", "num", totals.Untagged ? fmt(totals.Untagged) : "—"));
    foot.appendChild(el("td", "num strong " +
      (netPosition >= 0 ? "settlement-receivable" : "settlement-payable"),
      settlementPosition(netPosition)));
    table.appendChild(foot);
    wrap.appendChild(table);
  }

  // ---------- Transactions ----------

  function transactionMonths() {
    return state.transactionSource === "bank" ? account.months : data.months;
  }

  function transactionStatementRange(month) {
    var sourceRows = state.transactionSource === "bank"
      ? account.transactions : data.transactions;
    var dates = sourceRows.filter(function (t) {
      return t.month === month && t.date;
    }).map(function (t) { return t.date; }).sort();
    if (!dates.length) return "";
    var first = dates[0], last = dates[dates.length - 1];
    if (first === last) return shortDate(first, true);
    var crossYear = first.slice(0, 4) !== last.slice(0, 4);
    if (first.slice(0, 7) === last.slice(0, 7)) {
      return String(parseInt(first.slice(8), 10)) + "–" + shortDate(last, true);
    }
    return shortDate(first, crossYear) + "–" + shortDate(last, true);
  }

  function periodLabel() {
    var p = state.period;
    if (p.mode === "all") return "All time";
    if (p.mode === "year") return "All of " + p.year;
    var key = p.year + "-" + p.month;
    return statementLabel(key) + " · " + transactionStatementRange(key);
  }
  function periodControlLabel() {
    var p = state.period;
    return p.mode === "month" ? statementLabel(p.year + "-" + p.month) : periodLabel();
  }

  function inPeriod(t) {
    var p = state.period;
    if (p.mode === "all") return true;
    if (p.mode === "year") return t.month.slice(0, 4) === p.year;
    return t.month === p.year + "-" + p.month;
  }

  function renderPeriod() {
    document.getElementById("period-label").textContent = periodControlLabel();

    var years = {};
    transactionMonths().forEach(function (m) {
      var y = m.slice(0, 4);
      if (!years[y]) years[y] = {};
      years[y][m.slice(5)] = true;
    });
    var yearList = Object.keys(years).sort();

    var yearWrap = document.getElementById("period-years");
    clear(yearWrap);
    yearList.forEach(function (y) {
      var active = state.period.mode !== "all" && state.period.year === y;
      var b = el("button", "period-year" + (active ? " active" : ""), y);
      setPressed(b, active);
      b.addEventListener("click", function () {
        state.period = { mode: "year", year: y, month: null };
        state.ledgerLimit = LEDGER_CAP;
        renderPeriod();
        renderLedger();
      });
      yearWrap.appendChild(b);
    });

    var monthWrap = document.getElementById("period-months");
    clear(monthWrap);
    var shownYear = state.period.mode === "all"
      ? yearList[yearList.length - 1] : state.period.year;
    MONTH_NAMES.forEach(function (name, idx) {
      var mm = ("0" + (idx + 1)).slice(-2);
      var has = years[shownYear] && years[shownYear][mm];
      var active = state.period.mode === "month" &&
        state.period.year === shownYear && state.period.month === mm;
      var b = el("button", "period-month" + (active ? " active" : ""), name);
      setPressed(b, active);
      if (!has) {
        b.disabled = true;
        b.title = "No statement for " + name + " " + shownYear;
      } else {
        b.addEventListener("click", function () {
          state.period = { mode: "month", year: shownYear, month: mm };
          state.ledgerLimit = LEDGER_CAP;
          renderPeriod();
          renderLedger();
          closePeriod();
        });
      }
      monthWrap.appendChild(b);
    });

    var allButton = document.getElementById("period-all");
    var allActive = state.period.mode === "all";
    allButton.className = "period-all" + (allActive ? " active" : "");
    setPressed(allButton, allActive);
  }

  function closePeriod() {
    document.getElementById("period-menu").classList.add("hidden");
    document.getElementById("period-btn").setAttribute("aria-expanded", "false");
  }

  function matchesLedgerFilters(t, ignorePeriod) {
    var q = state.search.trim().toLowerCase();
    if (state.idFilter && !state.idFilter[t.id]) return false;
    if (!ignorePeriod && !inPeriod(t)) return false;
    if (state.transactionSource === "card") {
      if (state.foodpandaOnly && !t.foodpanda) return false;
      if (state.shopeeOnly && !t.shopee) return false;
      if (state.grabOnly && !t.grab) return false;
    }
    if (!state.showExcluded && EXCLUDED[t.category]) return false;
    if (state.owner !== "All" && t.owner !== state.owner) return false;
    if (state.category !== "All" && t.category !== state.category) return false;
    if (state.reviewMode === "lady-unconfirmed" &&
        ((t.card || "").toUpperCase().indexOf("LADY") === -1 || SETTLED[t.ownerSource])) return false;
    if (state.reviewMode === "split-by-rule" &&
        (t.ownerSource !== "merchant-rule" ||
         (t.owner !== "Shared" && t.owner !== "Yx"))) return false;
    if (state.reviewMode === "category-overlap" &&
        ((((data.quality || {}).review || {}).categoryRuleOverlap || {}).ids || [])
          .indexOf(t.id) === -1) return false;
    if (state.reviewMode === "delivery-rides" &&
        !DELIVERY_RIDES.test(t.description)) return false;
    // Every row in a flagged group carries the check so any of them can be
    // opened and reviewed; the queue lists the group once, via its primary row.
    if (state.reviewMode === "suspicious" &&
        (!t.risk || t.risk.recognized || t.risk.primary === false)) return false;
    if (q && (transactionName(t) + " " + t.description + " " +
      t.category + " " + t.owner + " " +
      (t.card || "") + " " + (t.foreign || "") + " " +
      (t.remark || "")).toLowerCase().indexOf(q) === -1) return false;
    return true;
  }

  function filteredLedger() {
    return data.transactions.filter(function (t) {
      return matchesLedgerFilters(t, false);
    }).sort(function (a, b) { return (b.date || b.month).localeCompare(a.date || a.month); });
  }

  function matchesAccountLedgerFilters(t, ignorePeriod) {
    var q = state.search.trim().toLowerCase();
    if (!ignorePeriod && !inPeriod(t)) return false;
    if (state.category !== "All" && t.flow !== state.category) return false;
    if (state.bankDirection !== "all" && t.direction !== state.bankDirection) return false;
    if (state.bankExcludeInternal &&
        window.FinanceGrouping.accountIsInternalMovement(t)) return false;
    var review = t.accountReview || {};
    if (state.bankReview === "needs-review" &&
        (!review.requiresReview || review.reviewed)) return false;
    if (state.bankReview === "reviewed" && !review.reviewed) return false;
    if (state.bankReview === "new-counterparty" && !review.firstCounterparty) return false;
    if (state.bankReview === "large-unusual" &&
        !(review.checks || []).some(function (check) {
          return check.indexOf("large-") === 0 || check === "derived-amount" ||
            check === "unverified-source";
        })) return false;
    if (state.bankReview === "unclassified" &&
        (review.checks || []).indexOf("unclassified") === -1) return false;
    if (state.bankReview === "possible-duplicate" &&
        (review.checks || []).indexOf("possible-duplicate") === -1) return false;
    if (q && ((t.counterparty || "") + " " + t.description + " " + t.flow + " " +
      t.direction + " " + (review.reasons || []).join(" ") + " " +
      (t.provenance && t.provenance.sourceFile || "")).toLowerCase().indexOf(q) === -1) {
      return false;
    }
    return true;
  }

  function filteredAccountLedger() {
    return account.transactions.filter(function (t) {
      return matchesAccountLedgerFilters(t, false);
    }).sort(function (a, b) {
      return window.FinanceGrouping.accountSourceOrder(b).localeCompare(
        window.FinanceGrouping.accountSourceOrder(a));
    });
  }

  function appendAverageComparison(headline, currentCost) {
    var comparison = el("div", "transaction-average");
    if (state.period.mode !== "month") {
      comparison.appendChild(el("div", "transaction-average-note",
        "Select one month to compare with its history."));
      headline.appendChild(comparison);
      return;
    }

    var currentMonth = state.period.year + "-" + state.period.month;
    var allPriorMonths = data.months.filter(function (month) {
      return month < currentMonth;
    });
    if (!allPriorMonths.length) {
      comparison.appendChild(el("div", "transaction-average-note",
        "No earlier statement months available."));
      headline.appendChild(comparison);
      return;
    }
    var grid = el("div", "transaction-average-grid");
    var renderedSpans = [];
    [6, 12].forEach(function (requestedMonths) {
      var priorMonths = allPriorMonths.slice(-requestedMonths);
      // With under 12 months of history both windows hold the same months and
      // the "12M" tile rendered as a duplicate of the 6M one.
      if (renderedSpans.indexOf(priorMonths.length) !== -1) return;
      renderedSpans.push(priorMonths.length);
      var comparisonRows = data.transactions.filter(function (transaction) {
        return priorMonths.indexOf(transaction.month) !== -1 &&
          matchesLedgerFilters(transaction, true);
      });
      var average = window.FinanceGrouping.averageForMonths(
        comparisonRows, priorMonths, EXCLUDED
      );
      var difference = roundMoney(currentCost - average);
      var direction = difference > 0 ? "above" : difference < 0 ? "below" : "in line";
      var tone = difference > 0 ? "higher" : difference < 0 ? "lower" : "even";
      var item = el("div", "transaction-average-item");
      item.appendChild(el("span", "", priorMonths.length + "M avg"));
      item.appendChild(el("strong", "", fmt(average)));
      var variance = Math.abs(average) >= 0.01
        ? (Math.abs(difference) / Math.abs(average) * 100).toFixed(1) + "% " + direction
        : (difference === 0 ? "in line" : fmt(Math.abs(difference)) + " " + direction);
      item.appendChild(el("small", tone, variance));
      grid.appendChild(item);
    });
    comparison.appendChild(grid);
    headline.appendChild(comparison);
  }

  // The summary panel and the table foot have to agree, so both read the same
  // figure from the same call: "Net cost" always drops Payment and Rebates.
  // With "show excluded" on, what those rows contribute is reported beside it
  // instead of being silently folded into a second, differently-signed "Net".
  function appendLedgerNet(foot, rows) {
    var totals = window.FinanceGrouping.summarize(rows, EXCLUDED);
    var text = "Net cost " + fmt(totals.netCost);
    if (totals.excludedCount) {
      text += " · excluded rows " + fmt(totals.excludedTotal);
    }
    foot.appendChild(el("span", "", text));
  }

  function renderTransactionSummary(rows) {
    var summary = document.getElementById("transaction-summary");
    clear(summary);
    summary.classList.remove("bank-summary");
    var totals = window.FinanceGrouping.summarize(rows, EXCLUDED);
    if (!totals.count) {
      summary.appendChild(el("div", "transaction-summary-empty",
        "No cost breakdown for this view."));
      return;
    }

    var headline = el("div", "transaction-summary-total");
    headline.appendChild(el("span", "transaction-summary-label", "Net cost"));
    headline.appendChild(el("strong", totals.netCost < 0 ? "credit" : "",
      fmt(totals.netCost)));
    headline.appendChild(el("small", "",
      totals.count + " cost transaction" + (totals.count === 1 ? "" : "s") +
      " · after refunds"));
    appendAverageComparison(headline, totals.netCost);
    summary.appendChild(headline);

    function appendBreakdown(title, totals, order, limit, kind) {
      var section = el("div", "transaction-breakdown");
      section.appendChild(el("span", "transaction-summary-label", title));
      var entries = Object.keys(totals).map(function (name) {
        return { name: name, amount: roundMoney(totals[name]) };
      }).filter(function (item) {
        return Math.abs(item.amount) >= 0.01;
      });
      entries.sort(function (a, b) {
        if (order) {
          var ai = order.indexOf(a.name), bi = order.indexOf(b.name);
          ai = ai === -1 ? order.length : ai;
          bi = bi === -1 ? order.length : bi;
          if (ai !== bi) return ai - bi;
        }
        return b.amount - a.amount;
      });
      if (limit && entries.length > limit) {
        var remainder = entries.slice(limit).reduce(function (total, item) {
          return total + item.amount;
        }, 0);
        entries = entries.slice(0, limit);
        if (Math.abs(remainder) >= 0.01) {
          entries.push({ name: "Other categories", amount: roundMoney(remainder) });
        }
      }
      var maxAmount = entries.reduce(function (largest, item) {
        return Math.max(largest, Math.abs(item.amount));
      }, 0.01);
      entries.forEach(function (item) {
        var row = el("div", "transaction-breakdown-row");
        var name = item.name === "Untagged" ? "Unassigned" : item.name;
        row.appendChild(el("span", "transaction-breakdown-name", name));
        var track = el("span", "transaction-breakdown-track");
        var fill = el("span", "transaction-breakdown-fill" +
          (item.amount < 0 ? " refund" : ""));
        fill.style.width = Math.max(3, Math.abs(item.amount) / maxAmount * 100) + "%";
        if (kind === "category" && item.name !== "Other categories") {
          fill.style.backgroundColor = categoryColor(item.name);
        }
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el("strong", item.amount < 0 ? "credit" : "", fmt(item.amount)));
        section.appendChild(row);
      });
      summary.appendChild(section);
    }

    appendBreakdown("By category", totals.categoryTotals, null, 4, "category");
    appendBreakdown("By owner", totals.ownerTotals, OWNER_ORDER, null, "owner");
  }

  function appendBankAverageComparison(headline, currentSpending) {
    var comparison = el("div", "transaction-average");
    if (state.period.mode !== "month") {
      comparison.appendChild(el("div", "transaction-average-note",
        "Select one month to compare with its history."));
      headline.appendChild(comparison);
      return;
    }
    var currentMonth = state.period.year + "-" + state.period.month;
    var allPriorMonths = account.months.filter(function (month) {
      return month < currentMonth;
    });
    if (!allPriorMonths.length) {
      comparison.appendChild(el("div", "transaction-average-note",
        "No earlier account statements available."));
      headline.appendChild(comparison);
      return;
    }
    var grid = el("div", "transaction-average-grid");
    var renderedSpans = [];
    [6, 12].forEach(function (requestedMonths) {
      var priorMonths = allPriorMonths.slice(-requestedMonths);
      // Same guard as the card tiles: short history makes both windows equal.
      if (renderedSpans.indexOf(priorMonths.length) !== -1) return;
      renderedSpans.push(priorMonths.length);
      var rows = account.transactions.filter(function (transaction) {
        return priorMonths.indexOf(transaction.month) !== -1;
      });
      var average = window.FinanceGrouping.averageAccountSpending(rows, priorMonths);
      var difference = roundMoney(currentSpending - average);
      var direction = difference > 0 ? "above" : difference < 0 ? "below" : "in line";
      var tone = difference > 0 ? "higher" : difference < 0 ? "lower" : "even";
      var item = el("div", "transaction-average-item");
      item.appendChild(el("span", "", priorMonths.length + "M spend avg"));
      item.appendChild(el("strong", "", fmt(average)));
      var variance = Math.abs(average) >= 0.01
        ? (Math.abs(difference) / Math.abs(average) * 100).toFixed(1) + "% " + direction
        : (difference === 0 ? "in line" : fmt(Math.abs(difference)) + " " + direction);
      item.appendChild(el("small", tone, variance));
      grid.appendChild(item);
    });
    comparison.appendChild(grid);
    headline.appendChild(comparison);
  }

  function appendBankBreakdown(summary, title, totals, limit, toneByName) {
    var section = el("div", "transaction-breakdown");
    section.appendChild(el("span", "transaction-summary-label", title));
    var entries = Object.keys(totals).map(function (name) {
      return { name: name, amount: roundMoney(totals[name]) };
    }).filter(function (item) { return item.amount >= 0.01; }).sort(function (a, b) {
      return b.amount - a.amount;
    });
    if (limit && entries.length > limit) {
      var remainder = entries.slice(limit).reduce(function (total, item) {
        return total + item.amount;
      }, 0);
      entries = entries.slice(0, limit);
      if (remainder >= 0.01) entries.push({ name: "Other flows", amount: roundMoney(remainder) });
    }
    var maxAmount = entries.reduce(function (largest, item) {
      return Math.max(largest, item.amount);
    }, 0.01);
    entries.forEach(function (item) {
      var row = el("div", "transaction-breakdown-row");
      row.appendChild(el("span", "transaction-breakdown-name", item.name));
      var track = el("span", "transaction-breakdown-track");
      var fill = el("span", "transaction-breakdown-fill");
      fill.style.width = Math.max(3, item.amount / maxAmount * 100) + "%";
      if (toneByName && toneByName[item.name]) fill.style.backgroundColor = toneByName[item.name];
      track.appendChild(fill);
      row.appendChild(track);
      row.appendChild(el("strong", "", fmt(item.amount)));
      section.appendChild(row);
    });
    summary.appendChild(section);
  }

  function appendBankReviewSummary(summary, rows) {
    var section = el("div", "transaction-breakdown bank-review-summary");
    section.appendChild(el("span", "transaction-summary-label", "Review status"));
    var flagged = rows.filter(function (transaction) {
      return transaction.accountReview && transaction.accountReview.requiresReview;
    });
    var pending = flagged.filter(function (transaction) {
      return !transaction.accountReview.reviewed;
    });
    var reviewed = rows.filter(function (transaction) {
      return transaction.accountReview && transaction.accountReview.reviewed;
    });
    section.appendChild(el("strong", pending.length ? "review-pending" : "review-clear",
      pending.length ? pending.length + (pending.length === 1
        ? " needs review" : " need review") : "All clear"));
    section.appendChild(el("small", "",
      reviewed.length + " checked · " + flagged.length + " automatically flagged"));
    if (pending.length) {
      var reviewLink = el("a", "bank-review-link",
        "Review " + (pending.length === 1 ? "transaction" : "transactions") + " →");
      reviewLink.href = "#bank-transactions";
      reviewLink.addEventListener("click", function () {
        state.bankReview = "needs-review";
        state.bankDirection = "all";
        state.bankExcludeInternal = false;
        state.category = "All";
        state.search = "";
        // Grouping must reset with the other filters: a grouped ledger shows
        // counterparty rollups, not the individual rows needing review.
        state.groupPurchases = false;
        if (groupToggle) groupToggle.set(false);
        state.ledgerLimit = LEDGER_CAP;
        document.getElementById("bank-review-filter").value = state.bankReview;
        document.getElementById("bank-exclude-internal").checked = false;
        document.getElementById("search").value = "";
        populateTransactionCategoryFilter();
        Array.prototype.forEach.call(
          document.getElementById("bank-direction-pills").children,
          function (button) {
            var active = button.textContent === "All";
            button.classList.toggle("active", active);
            setPressed(button, active);
          }
        );
        renderLedger();
      });
      section.appendChild(reviewLink);
    }
    var progress = el("span", "bank-review-progress");
    var fill = el("span", "");
    fill.style.width = rows.length
      ? Math.min(100, reviewed.length / rows.length * 100) + "%" : "0%";
    progress.appendChild(fill);
    section.appendChild(progress);
    summary.appendChild(section);
  }

  function renderAccountSummary(rows) {
    var summary = document.getElementById("transaction-summary");
    clear(summary);
    var totals = window.FinanceGrouping.summarizeAccount(rows);
    var periodRows = account.transactions.filter(function (transaction) {
      return inPeriod(transaction);
    });
    var statementTotals = window.FinanceGrouping.summarizeAccount(periodRows);
    if (!totals.count) {
      summary.appendChild(el("div", "transaction-summary-empty",
        "No bank transactions match this view."));
      return;
    }
    summary.classList.add("bank-summary");
    var headline = el("div", "transaction-summary-total");
    headline.appendChild(el("span", "transaction-summary-label", "Closing balance"));
    headline.appendChild(el("strong", "", statementTotals.closingBalance === null
      ? "—" : fmt(statementTotals.closingBalance)));
    var reconciled = statementTotals.reconciliationGap !== null &&
      Math.abs(statementTotals.reconciliationGap) < 0.01;
    headline.appendChild(el("small", reconciled ? "review-clear" : "review-pending",
      (statementTotals.openingBalance === null ? "Opening unavailable" :
        "Opened " + fmt(statementTotals.openingBalance)) +
      (reconciled ? " · reconciled ✓" : " · reconciliation gap " +
        fmt(statementTotals.reconciliationGap || 0)) +
      (rows.length !== periodRows.length ? " · " + rows.length + " shown" : "")));
    appendBankAverageComparison(headline, statementTotals.nonTransferSpending);
    summary.appendChild(headline);
    appendBankBreakdown(summary, "Money movement", {
      "Money in": totals.deposits,
      "Money out": totals.withdrawals
    }, null, { "Money in": "var(--green)", "Money out": "var(--red)" });
    appendBankBreakdown(summary, "Non-transfer spending", totals.spendingFlows, 4);
    appendBankReviewSummary(summary, periodRows);
  }

  function renderGroupedAccountLedger(body, rows, showYear) {
    var groups = window.FinanceGrouping.groupAccountTransactions(rows);
    document.getElementById("ledger-date-head").textContent = "Latest";
    document.getElementById("ledger-description-head").textContent = "Counterparty";
    document.getElementById("ledger-category-head").textContent = "Flow";
    document.getElementById("ledger-owner-head").textContent = "In / Out";
    document.getElementById("ledger-remark-head").textContent = "Transactions";
    document.getElementById("ledger-amount-head").textContent = "Grouped amount";
    groups.slice(0, state.ledgerLimit).forEach(function (group) {
      var tr = document.createElement("tr");
      tr.className = "grouped-purchase-row" + (group.reviewCount ? " risk-row risk-medium" : "");
      var d = group.lastDate ? group.lastDate.slice(8, 10) + " " +
        MONTH_NAMES[parseInt(group.lastDate.slice(5, 7), 10) - 1] : "—";
      if (showYear && group.lastDate) d += " " + group.lastDate.slice(2, 4);
      tr.appendChild(el("td", "col-date", d));
      var description = el("td", "bank-counterparty", group.label);
      if (group.reviewCount) description.appendChild(el(
        "span", "risk-badge risk-medium", group.reviewCount + " review"));
      tr.appendChild(description);
      var flow = el("td", "col-cat");
      flow.appendChild(el("span", "cat-pill bank-flow", group.flow));
      tr.appendChild(flow);
      var direction = el("td", "col-owner");
      direction.appendChild(el("span", "bank-direction " +
        (group.direction === "deposit" ? "money-in" : "money-out"),
        group.direction === "deposit" ? "Money in" : "Money out"));
      tr.appendChild(direction);
      var countCell = el("td", "col-remark grouped-count",
        group.count + " row" + (group.count === 1 ? "" : "s"));
      if (group.reviewCount && editor.available) {
        var batch = el("button", "batch-review-button",
          "Review all " + group.reviewCount);
        batch.title = "Mark every flagged row from " + group.label + " as reviewed";
        batch.addEventListener("click", function (event) {
          event.stopPropagation();
          saveAccountReviewBatch(group.reviewIds.slice(), batch);
        });
        countCell.appendChild(batch);
      }
      tr.appendChild(countCell);
      tr.appendChild(el("td", "col-amt bank-amount" +
        (group.direction === "deposit" ? " credit" : ""),
        (group.direction === "deposit" ? "+" : "−") +
        group.amount.toLocaleString("en-SG",
          { minimumFractionDigits: 2, maximumFractionDigits: 2 })));
      body.appendChild(tr);
    });
    var foot = document.getElementById("ledger-foot");
    clear(foot);
    foot.appendChild(el("span", "", groups.length + " counterpart" +
      (groups.length === 1 ? "y" : "ies") + " · " + rows.length + " transactions"));
    var totals = window.FinanceGrouping.summarizeAccount(rows);
    foot.appendChild(el("span", "", "Net movement " + fmt(totals.netMovement)));
    document.getElementById("ledger-hint").textContent = periodLabel() +
      " · grouped by counterparty";
  }

  function renderAccountLedger(body) {
    var rows = filteredAccountLedger();
    renderAccountSummary(rows);
    var showYear = state.period.mode !== "month";
    document.querySelector(".ledger").classList.add("bank-ledger");
    if (state.groupPurchases) {
      renderGroupedAccountLedger(body, rows, showYear);
      return;
    }
    document.getElementById("ledger-date-head").textContent = "Date";
    document.getElementById("ledger-description-head").textContent = "Bank transaction";
    document.getElementById("ledger-category-head").textContent = "Type";
    document.getElementById("ledger-owner-head").textContent = "In / Out";
    document.getElementById("ledger-remark-head").textContent = "Amount";
    document.getElementById("ledger-amount-head").textContent = "Balance after";
    rows.slice(0, state.ledgerLimit).forEach(function (t) {
      var tr = document.createElement("tr");
      if (t.accountReview && t.accountReview.requiresReview &&
          !t.accountReview.reviewed) {
        tr.classList.add("risk-row", "risk-medium");
      }
      var d = t.date ? t.date.slice(8, 10) + " " +
        MONTH_NAMES[parseInt(t.date.slice(5, 7), 10) - 1] : "—";
      if (showYear && t.date) d += " " + t.date.slice(2, 4);
      tr.appendChild(el("td", "col-date", d));
      var description = el("td", "bank-counterparty",
        t.counterparty || window.FinanceGrouping.accountCounterparty(t.description));
      description.title = t.description;
      if (t.accountReview && t.accountReview.requiresReview) {
        description.appendChild(el("span", t.accountReview.reviewed
          ? "review-badge reviewed" : "risk-badge risk-medium",
        t.accountReview.reviewed ? "Reviewed" : "Review"));
      } else if (t.accountReview && t.accountReview.reviewed) {
        description.appendChild(el("span", "review-badge reviewed", "Reviewed"));
      }
      description.appendChild(el("small", "bank-description-raw", t.description));
      tr.appendChild(description);
      var flow = el("td", "col-cat");
      flow.appendChild(el("span", "cat-pill bank-flow", t.flow || "Other"));
      tr.appendChild(flow);
      var direction = el("td", "col-owner");
      direction.appendChild(el("span", "bank-direction " +
        (t.direction === "deposit" ? "money-in" : "money-out"),
        t.direction === "deposit" ? "Money in" : "Money out"));
      tr.appendChild(direction);
      var deposit = t.direction === "deposit";
      tr.appendChild(el("td", "col-remark bank-amount" + (deposit ? " credit" : ""),
        (deposit ? "+" : "−") + t.amount.toLocaleString("en-SG",
          { minimumFractionDigits: 2, maximumFractionDigits: 2 })));
      tr.appendChild(el("td", "col-amt bank-balance", fmt(t.balance)));
      appendExpandableRow(body, tr, t, 6, [], "account");
    });
    if (!rows.length) {
      var emptyRow = document.createElement("tr");
      var emptyCell = el("td", "empty", "No bank transactions match");
      emptyCell.colSpan = 6;
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
    }
    var totals = window.FinanceGrouping.summarizeAccount(rows);
    var foot = document.getElementById("ledger-foot");
    clear(foot);
    var shown = Math.min(rows.length, state.ledgerLimit);
    var left = rows.length + " bank transaction" + (rows.length === 1 ? "" : "s");
    if (rows.length > shown) left += " (showing " + shown + ")";
    foot.appendChild(el("span", "", left));
    if (rows.length > shown) {
      var more = el("button", "ledger-more", "Load " +
        Math.min(LEDGER_CAP, rows.length - shown) + " more");
      more.addEventListener("click", function () {
        state.ledgerLimit += LEDGER_CAP;
        renderLedger();
      });
      foot.appendChild(more);
    }
    foot.appendChild(el("span", "", "Net movement " + fmt(totals.netMovement)));
    document.getElementById("ledger-hint").textContent = periodLabel() + " · bank account";
  }

  function groupedPurchases(rows) {
    return window.FinanceGrouping.groupPurchases(rows);
  }

  function renderGroupedLedger(body, rows, showYear) {
    var groups = groupedPurchases(rows);
    document.getElementById("ledger-date-head").textContent = "Latest";
    document.getElementById("ledger-description-head").textContent = "Merchant";
    document.getElementById("ledger-remark-head").textContent = "Purchases";
    document.getElementById("ledger-amount-head").textContent = "Grouped amount";
    groups.slice(0, state.ledgerLimit).forEach(function (group) {
      var tr = document.createElement("tr");
      tr.className = "grouped-purchase-row";
      var d = group.lastDate && group.lastDate.length >= 10
        ? group.lastDate.slice(8, 10) + " " +
          MONTH_NAMES[parseInt(group.lastDate.slice(5, 7), 10) - 1]
        : statementLabel(group.lastDate);
      if (showYear && group.lastDate && group.lastDate.length >= 10) {
        d += " " + group.lastDate.slice(2, 4);
      }
      tr.appendChild(el("td", "col-date", d));
      var description = el("td", "", group.label);
      description.title = "Grouped by normalized statement merchant";
      if (group.riskCount) {
        description.appendChild(el("span", "risk-badge risk-medium", "Check"));
      }
      addMerchantLogo(description, group.label);
      tr.appendChild(description);
      var category = el("td", "col-cat");
      category.appendChild(el("span", "cat-pill " + catClass(group.category), group.category));
      tr.appendChild(category);
      var owner = el("td", "col-owner");
      owner.appendChild(buildOwnerPicker(group.ids || [], group.owner, group.label));
      tr.appendChild(owner);
      tr.appendChild(el("td", "col-remark grouped-count",
        window.FinanceGrouping.groupCountLabel(group)));
      tr.appendChild(el("td", "col-amt" + (group.amount < 0 ? " credit" : ""),
        fmt(group.amount)));
      body.appendChild(tr);
    });
    if (!groups.length) {
      var emptyRow = document.createElement("tr");
      var emptyCell = el("td", "empty", "No transactions match");
      emptyCell.colSpan = 6;
      emptyRow.appendChild(emptyCell);
      body.appendChild(emptyRow);
    }
    var foot = document.getElementById("ledger-foot");
    clear(foot);
    var shown = Math.min(groups.length, state.ledgerLimit);
    var left = groups.length + " merchant group" + (groups.length === 1 ? "" : "s") +
      " · " + rows.length + " transaction" + (rows.length === 1 ? "" : "s");
    if (groups.length > shown) left += " (showing " + shown + ")";
    foot.appendChild(el("span", "", left));
    if (groups.length > shown) {
      var more = el("button", "ledger-more", "Load " +
        Math.min(LEDGER_CAP, groups.length - shown) + " more");
      more.addEventListener("click", function () {
        state.ledgerLimit += LEDGER_CAP;
        renderLedger();
      });
      foot.appendChild(more);
    }
    appendLedgerNet(foot, rows);
    document.getElementById("ledger-hint").textContent = periodLabel() +
      " · grouped by merchant" + (state.foodpandaOnly ? " · Foodpanda" : "") +
      (state.shopeeOnly ? " · Shopee" : "") +
      (state.grabOnly ? " · Grab" : "");
  }

  function syncSuspiciousFilterButton() {
    var button = document.getElementById("suspicious-filter");
    if (!button) return;
    var active = state.transactionSource === "bank"
      ? state.bankReview === "needs-review"
      : state.reviewMode === "suspicious";
    button.classList.toggle("active", active);
    setPressed(button, active);
    // While a review filter narrows the ledger to one statement month, offer
    // the whole history in a click: suspicious rows are rare, so the natural
    // next question is "and across all time?".
    var filtered = state.transactionSource === "bank"
      ? state.bankReview !== "all"
      : state.reviewMode === "suspicious";
    var viewAll = document.getElementById("view-all-filter");
    if (!viewAll) return;
    var show = filtered && state.period.mode === "month";
    viewAll.classList.toggle("hidden", !show);
    if (!show) return;
    var allTime = state.transactionSource === "bank"
      ? account.transactions.filter(function (t) {
          return matchesAccountLedgerFilters(t, true);
        }).length
      : data.transactions.filter(function (t) {
          return matchesLedgerFilters(t, true);
        }).length;
    viewAll.textContent = "All time (" + allTime + ")";
  }

  function renderLedger() {
    syncSuspiciousFilterButton();
    var body = document.getElementById("ledger-body");
    clear(body);
    if (state.transactionSource === "bank") {
      renderAccountLedger(body);
      return;
    }
    var rows = filteredLedger();
    renderTransactionSummary(rows);
    var showYear = state.period.mode !== "month";
    document.querySelector(".ledger").classList.remove("bank-ledger");
    document.getElementById("ledger-category-head").textContent = "Category";
    document.getElementById("ledger-owner-head").textContent = "Owner";
    if (state.groupPurchases) {
      renderGroupedLedger(body, rows, showYear);
      return;
    }
    document.getElementById("ledger-date-head").textContent = "Date";
    document.getElementById("ledger-description-head").textContent = "Description";
    document.getElementById("ledger-remark-head").textContent = "Remarks";
    document.getElementById("ledger-amount-head").textContent = "Amount";
    rows.slice(0, state.ledgerLimit).forEach(function (t) {
      var tr = document.createElement("tr");
      if (t.risk && !t.risk.recognized) {
        tr.classList.add("risk-row", "risk-" + t.risk.severity);
      }
      var d = t.date ? t.date.slice(8, 10) + " " + MONTH_NAMES[parseInt(t.date.slice(5, 7), 10) - 1] : "—";
      if (showYear && t.date) d += " " + t.date.slice(2, 4);
      tr.appendChild(el("td", "col-date", d));
      var shopeeItems = t.shopee && Array.isArray(t.shopee.items)
        ? t.shopee.items.filter(Boolean) : [];
      var grabReceipts = t.grab && Array.isArray(t.grab.receipts) ? t.grab.receipts : [];
      var grabName = grabTransactionName(t);
      var tdDesc = document.createElement("td");
      if (grabReceipts.length && !t.displayName) {
        tdDesc.className = "purchase-description";
        tdDesc.appendChild(el("span", "purchase-description-primary", grabName));
        var grabSecondaryNames = [];
        grabReceipts.forEach(function (receipt) {
          var name = receipt.service;
          if (name && grabSecondaryNames.indexOf(name) === -1) grabSecondaryNames.push(name);
        });
        var grabMeta = el("small", "purchase-description-secondary",
          (grabSecondaryNames.join(" · ") || "Grab") + " · Receipt matched");
        if (t.grab.corporate) {
          grabMeta.appendChild(el("span", "source-badge corporate-source", "Corporate · excluded"));
        }
        tdDesc.appendChild(grabMeta);
      } else if (t.grab && t.grab.status === "unreconciled" && !t.displayName) {
        tdDesc.className = "purchase-description";
        tdDesc.appendChild(el("span", "purchase-description-primary", transactionName(t)));
        tdDesc.appendChild(el("small", "purchase-description-secondary",
          "Wallet funding · unreconciled"));
      } else if (shopeeItems.length && !t.displayName) {
        tdDesc.className = "purchase-description";
        tdDesc.appendChild(el("span", "purchase-description-primary", shopeeItems.join(" · ")));
        var shopMeta = el("small", "purchase-description-secondary", t.shopee.merchant);
        tdDesc.appendChild(shopMeta);
      } else {
        tdDesc.appendChild(document.createTextNode(transactionName(t)));
      }
      tdDesc.title = grabReceipts.length
        ? grabName + " · Grab receipt · Statement: " + t.description
        : shopeeItems.length
        ? shopeeItems.join(" · ") + " · Seller: " + t.shopee.merchant +
          " · Statement: " + t.description
        : (t.displayName || t.foodpanda || t.shopee || t.grab)
          ? transactionName(t) + " · Statement: " + t.description
          : t.description;
      if (t.risk && !t.risk.recognized) {
        tdDesc.appendChild(el("span", "risk-badge risk-" + t.risk.severity,
          t.risk.severity === "high" ? "Check now" : "Check"));
      }
      addMerchantLogo(tdDesc, t);
      tr.appendChild(tdDesc);
      var tdCat = el("td", "col-cat");
      tdCat.appendChild(el("span", "cat-pill " + catClass(t.category), t.category));
      tr.appendChild(tdCat);
      var tdOwner = el("td", "col-owner");
      tdOwner.appendChild(buildOwnerPicker([t.id], t.owner, transactionName(t)));
      tr.appendChild(tdOwner);
      var tdRemark = el("td", "col-remark");
      tdRemark.appendChild(buildRemarkInput(t));
      tr.appendChild(tdRemark);
      var credit = t.type !== "debit";
      tr.appendChild(el("td", "col-amt" + (credit ? " credit" : ""),
        (credit ? "+" : "-") + t.amount.toLocaleString("en-SG",
          { minimumFractionDigits: 2, maximumFractionDigits: 2 })));
      var details = [
        ["Date", t.date || statementLabel(t.month)],
        ["Posted", t.postedDate || t.date || statementLabel(t.month)],
        ["Statement", statementLabel(t.month)],
        ["Category", t.category],
        ["Owner", t.owner === "Untagged" ? "Unassigned" : t.owner],
        ["Remark", t.remark || "—"],
        ["Card", t.card || "UOB ONE CARD"],
        ["Type", t.type],
        ["Amount", (credit ? "+" : "-") + fmt(t.amount)],
        ["Source", t.provenance
          ? t.provenance.sourceFile +
            (t.provenance.page ? ", page " + t.provenance.page : "")
          : "legacy build"],
        ["Source status", t.provenance && t.provenance.verified ? "verified" : "needs review"],
        ["Transaction ID", t.id]
      ];
      if (t.risk) {
        details.splice(8, 0, [
          "Transaction check",
          (t.risk.recognized ? "recognized · " : "") + t.risk.reasons.join(" · ")
        ]);
      }
      appendExpandableRow(body, tr, t, 6, details, "ledger");
    });
    if (!rows.length) {
      var tr = document.createElement("tr");
      var td = el("td", "empty", "No transactions match");
      td.colSpan = 6;
      tr.appendChild(td);
      body.appendChild(tr);
    }
    var foot = document.getElementById("ledger-foot");
    clear(foot);
    var left = rows.length + " transaction" + (rows.length === 1 ? "" : "s");
    var shown = Math.min(rows.length, state.ledgerLimit);
    if (rows.length > shown) left += " (showing " + shown + ")";
    foot.appendChild(el("span", "", left));
    // Filter down to a review queue, then clear it in one click instead of one
    // drawer round trip per row.
    var bulk = buildOwnerBulkAction(rows);
    if (bulk) foot.appendChild(bulk);
    if (rows.length > shown) {
      var more = el("button", "ledger-more", "Load " +
        Math.min(LEDGER_CAP, rows.length - shown) + " more");
      more.addEventListener("click", function () {
        state.ledgerLimit += LEDGER_CAP;
        renderLedger();
      });
      foot.appendChild(more);
    }
    appendLedgerNet(foot, rows);
    document.getElementById("ledger-hint").textContent = periodLabel() +
      (state.foodpandaOnly ? " · Foodpanda" : "") +
      (state.shopeeOnly ? " · Shopee" : "") +
      (state.grabOnly ? " · Grab" : "") +
      (state.reviewMode === "lady-unconfirmed" ? " · Lady card needs confirmation" : "") +
      (state.reviewMode === "category-overlap" ? " · category rules overlap" : "") +
      (state.reviewMode === "delivery-rides" ? " · Grab + Foodpanda" : "");
    if (state.reviewMode === "suspicious") {
      document.getElementById("ledger-hint").textContent +=
        " · suspicious transaction checks";
    }
  }

  // ---------- Shell ----------

  function renderAll() {
    renderBankRules();
    renderFreshness();
    renderKpis();
    renderDataQuality();
    renderStacked();
    renderKeyMetrics();
    renderInsights();
    renderSpendingSummary();
    renderCategories();
    renderOutflows();
    // An edit can retire a category or introduce a new one, so the filter
    // options are rebuilt here rather than only on load.
    populateTransactionCategoryFilter();
    renderLedger();
    renderIncome();
    renderInsurance();
    renderGames();
    renderSplit();
    var idx = data.months.indexOf(state.month);
    document.getElementById("prev-month").disabled = idx <= 0;
    document.getElementById("next-month").disabled = idx >= data.months.length - 1;
  }

  function setMonth(m) {
    state.month = m;
    var monthSelect = document.getElementById("month-select");
    monthSelect.value = m;
    monthSelect.title = statementRange(m);
    renderKpis();
    renderStacked();
    renderKeyMetrics();
    renderInsights();
    renderSpendingSummary();
    renderCategories();
    renderOutflows();
    renderLedger();
    var idx = data.months.indexOf(m);
    document.getElementById("prev-month").disabled = idx <= 0;
    document.getElementById("next-month").disabled = idx >= data.months.length - 1;
  }

  function setTab(name) {
    state.tab = name;
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
      var active = t.getAttribute("data-tab") === name;
      t.classList.toggle("active", active);
      t.setAttribute("aria-selected", active ? "true" : "false");
    });
    Array.prototype.forEach.call(document.querySelectorAll(".pane"), function (p) {
      var active = p.id === "pane-" + name;
      p.classList.toggle("hidden", !active);
      p.setAttribute("aria-hidden", active ? "false" : "true");
    });
    // Transactions has its own period picker, so the header month nav steps aside.
    var monthTabs = { overview: 1 };
    document.getElementById("month-nav").classList.toggle("hidden", !monthTabs[name]);
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    var btn = document.getElementById("theme-toggle");
    var toLight = theme === "dark";
    clear(btn);
    btn.appendChild(icon(toLight ? "sun" : "moon"));
    btn.title = toLight ? "Switch to light mode" : "Switch to dark mode";
    btn.setAttribute("aria-label", btn.title);
  }

  function buildThemeToggle() {
    var btn = document.getElementById("theme-toggle");
    applyTheme(document.documentElement.getAttribute("data-theme") || "light");
    btn.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
      try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
      applyTheme(next);
    });
  }

  function buildControls() {
    buildThemeToggle();
    var tabButtons = Array.prototype.slice.call(document.querySelectorAll(".tab"));
    tabButtons.forEach(function (t, index) {
      t.addEventListener("click", function () { setTab(t.getAttribute("data-tab")); });
      t.addEventListener("keydown", function (e) {
        var target = null;
        if (e.key === "ArrowRight") target = (index + 1) % tabButtons.length;
        else if (e.key === "ArrowLeft") target = (index - 1 + tabButtons.length) % tabButtons.length;
        else if (e.key === "Home") target = 0;
        else if (e.key === "End") target = tabButtons.length - 1;
        if (target !== null) {
          e.preventDefault();
          tabButtons[target].focus();
          setTab(tabButtons[target].getAttribute("data-tab"));
        }
      });
    });

    var sel = document.getElementById("month-select");
    data.months.slice().reverse().forEach(function (m) {
      var opt = document.createElement("option");
      opt.value = m;
      opt.textContent = statementLabel(m);
      sel.appendChild(opt);
    });
    sel.value = state.month;
    sel.title = statementRange(state.month);
    sel.addEventListener("change", function () { setMonth(sel.value); });
    document.getElementById("prev-month").addEventListener("click", function () {
      var i = data.months.indexOf(state.month);
      if (i > 0) setMonth(data.months[i - 1]);
    });
    document.getElementById("next-month").addEventListener("click", function () {
      var i = data.months.indexOf(state.month);
      if (i < data.months.length - 1) setMonth(data.months[i + 1]);
    });

    var sourcePills = document.getElementById("transaction-source-pills");
    [
      { key: "card", label: "Cards" },
      { key: "bank", label: "Bank account" }
    ].forEach(function (source) {
      var button = el("button", "pill", source.label);
      button.setAttribute("data-source", source.key);
      button.addEventListener("click", function () { setTransactionSource(source.key); });
      sourcePills.appendChild(button);
    });

    var bankDirectionPills = document.getElementById("bank-direction-pills");
    [
      { key: "all", label: "All" },
      { key: "withdrawal", label: "Money out" },
      { key: "deposit", label: "Money in" }
    ].forEach(function (direction) {
      var button = el("button", "pill" +
        (state.bankDirection === direction.key ? " active" : ""), direction.label);
      setPressed(button, state.bankDirection === direction.key);
      button.addEventListener("click", function () {
        state.bankDirection = direction.key;
        state.ledgerLimit = LEDGER_CAP;
        Array.prototype.forEach.call(bankDirectionPills.children, function (child) {
          var active = child.textContent === direction.label;
          child.classList.toggle("active", active);
          setPressed(child, active);
        });
        renderLedger();
      });
      bankDirectionPills.appendChild(button);
    });
    var bankReviewFilter = document.getElementById("bank-review-filter");
    bankReviewFilter.value = state.bankReview;
    bankReviewFilter.addEventListener("change", function () {
      state.bankReview = bankReviewFilter.value;
      state.ledgerLimit = LEDGER_CAP;
      renderLedger();
    });
    var bankExcludeInternal = document.getElementById("bank-exclude-internal");
    bankExcludeInternal.checked = state.bankExcludeInternal;
    bankExcludeInternal.addEventListener("change", function () {
      state.bankExcludeInternal = bankExcludeInternal.checked;
      state.ledgerLimit = LEDGER_CAP;
      renderLedger();
    });
    // One button, one meaning per view: unresolved card checks, or bank rows
    // still needing review. It drives the same state the dropdown and the
    // overview drill-down use, so all three stay in sync.
    document.getElementById("suspicious-filter").addEventListener("click", function () {
      if (state.transactionSource === "bank") {
        state.bankReview = state.bankReview === "needs-review" ? "all" : "needs-review";
        bankReviewFilter.value = state.bankReview;
      } else {
        state.reviewMode = state.reviewMode === "suspicious" ? null : "suspicious";
      }
      state.ledgerLimit = LEDGER_CAP;
      renderLedger();
    });
    document.getElementById("view-all-filter").addEventListener("click", function () {
      // Widen the period, keep the review filter: the point is to see every
      // suspicious row in the history, not to drop the filter.
      state.period = { mode: "all", year: state.period.year, month: state.period.month };
      state.ledgerLimit = LEDGER_CAP;
      renderPeriod();
      renderLedger();
    });

    var pills = document.getElementById("owner-pills");
    ["All"].concat(OWNER_ORDER).forEach(function (o) {
      var b = el("button", "pill" + (o === state.owner ? " active" : ""), o);
      setPressed(b, o === state.owner);
      b.addEventListener("click", function () {
        state.owner = o;
        // Insight drill-down modes end when the user re-filters, but the
        // suspicious toggle is an ordinary filter and survives.
        if (state.reviewMode !== "suspicious") state.reviewMode = null;
        state.ledgerLimit = LEDGER_CAP;
        Array.prototype.forEach.call(pills.children, function (c) {
          var active = c.textContent === o;
          c.classList.toggle("active", active);
          setPressed(c, active);
        });
        renderLedger();
      });
      pills.appendChild(b);
    });

    var catSel = document.getElementById("category-filter");
    populateTransactionCategoryFilter();
    catSel.addEventListener("change", function () {
      state.category = catSel.value;
      if (state.reviewMode !== "suspicious") state.reviewMode = null;
      state.ledgerLimit = LEDGER_CAP;
      renderLedger();
    });

    var searchInput = document.getElementById("search");
    var chip = el("button", "pill active hidden", "");
    chip.id = "insight-filter-chip";
    chip.type = "button";
    chip.addEventListener("click", function () {
      setIdFilter(null);
      state.ledgerLimit = LEDGER_CAP;
      renderLedger();
    });
    searchInput.parentNode.insertBefore(chip, searchInput.nextSibling);

    searchInput.addEventListener("input", function (e) {
      state.search = e.target.value;
      state.reviewMode = null;
      // Searching means you want the whole ledger back, not a subset of an
      // insight's rows; drop the id filter rather than silently intersect.
      setIdFilter(null);
      state.ledgerLimit = LEDGER_CAP;
      renderLedger();
    });
    document.getElementById("foodpanda-filter").addEventListener("click", function () {
      state.foodpandaOnly = !state.foodpandaOnly;
      if (state.foodpandaOnly) {
        state.shopeeOnly = false;
        state.grabOnly = false;
      }
      state.ledgerLimit = LEDGER_CAP;
      syncTransactionSourceControls();
      renderLedger();
    });
    document.getElementById("shopee-filter").addEventListener("click", function () {
      state.shopeeOnly = !state.shopeeOnly;
      if (state.shopeeOnly) {
        state.foodpandaOnly = false;
        state.grabOnly = false;
      }
      state.ledgerLimit = LEDGER_CAP;
      syncTransactionSourceControls();
      renderLedger();
    });
    document.getElementById("grab-filter").addEventListener("click", function () {
      state.grabOnly = !state.grabOnly;
      if (state.grabOnly) {
        state.foodpandaOnly = false;
        state.shopeeOnly = false;
      }
      state.ledgerLimit = LEDGER_CAP;
      syncTransactionSourceControls();
      renderLedger();
    });
    document.getElementById("show-excluded").addEventListener("change", function (e) {
      state.showExcluded = e.target.checked;
      state.ledgerLimit = LEDGER_CAP;
      renderLedger();
    });

    var periodBtn = document.getElementById("period-btn");
    var periodMenu = document.getElementById("period-menu");
    periodBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var opening = periodMenu.classList.contains("hidden");
      periodMenu.classList.toggle("hidden", !opening);
      periodBtn.setAttribute("aria-expanded", opening ? "true" : "false");
    });
    periodMenu.addEventListener("click", function (e) { e.stopPropagation(); });
    document.getElementById("period-all").addEventListener("click", function () {
      state.period = { mode: "all", year: state.period.year, month: state.period.month };
      state.ledgerLimit = LEDGER_CAP;
      renderPeriod();
      renderLedger();
      closePeriod();
    });
    document.addEventListener("click", closePeriod);
    var groupButton = document.getElementById("group-purchases-button");
    var groupLabel = document.getElementById("group-purchases-label");
    groupToggle = window.FinanceGrouping.bindToggle(groupButton, groupLabel, function (active) {
      state.groupPurchases = active;
      state.ledgerLimit = LEDGER_CAP;
      syncTransactionSourceControls();
      renderLedger();
    }, state.groupPurchases);
    syncTransactionSourceControls();
    var historyButton = document.getElementById("audit-history-button");
    historyButton.disabled = !editor.available;
    if (!editor.available) {
      historyButton.title = "Start scripts/serve.py to view change history.";
    }
    historyButton.addEventListener("click", openAuditHistory);
    var pastDialog = document.getElementById("insurance-past-dialog");
    document.getElementById("insurance-past-button").addEventListener("click", function () {
      if (typeof pastDialog.showModal === "function") pastDialog.showModal();
      else pastDialog.setAttribute("open", "");
    });
    document.getElementById("insurance-past-close").addEventListener("click", function () {
      pastDialog.close();
    });
    pastDialog.addEventListener("click", function (event) {
      var bounds = pastDialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) pastDialog.close();
    });
    var drawerShell = document.getElementById("transaction-drawer-shell");
    document.getElementById("transaction-drawer-close").addEventListener(
      "click", closeTransactionDrawer
    );
    document.getElementById("transaction-drawer-backdrop").addEventListener(
      "click", closeTransactionDrawer
    );
    drawerShell.addEventListener("keydown", function (event) {
      if (event.key !== "Tab" || !editor.drawerTransactionId) return;
      var focusable = Array.prototype.slice.call(
        document.getElementById("transaction-drawer").querySelectorAll(
          "button:not(:disabled), input:not(:disabled), select:not(:disabled), " +
          "textarea:not(:disabled), [tabindex]:not([tabindex='-1'])"
        )
      );
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    var auditShell = document.getElementById("audit-history-shell");
    document.getElementById("audit-history-close").addEventListener(
      "click", closeAuditHistory
    );
    document.getElementById("audit-history-backdrop").addEventListener(
      "click", closeAuditHistory
    );
    auditShell.addEventListener("keydown", function (event) {
      if (event.key !== "Tab" || !editor.auditOpen) return;
      var focusable = Array.prototype.slice.call(
        document.getElementById("audit-history-panel").querySelectorAll(
          "button:not(:disabled), [tabindex]:not([tabindex='-1'])"
        )
      );
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        closePeriod();
        closeTransactionDrawer();
        closeAuditHistory();
      }
    });
    setTab(state.tab);
    renderPeriod();
  }

  function loadJson(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
      return r.json();
    });
  }

  loadJson("data/transactions.json")
    .then(function (json) {
      data = json;
      applyIdentity();
      return loadJson("data/account_transactions.json");
    })
    .then(function (acct) {
      account = acct;
      if ((data.generationId || account.generationId) &&
          data.generationId !== account.generationId) {
        throw new Error("card and account data belong to different import generations");
      }
      return Promise.all([
        loadJson("api/status").catch(function () { return { editable: false }; }),
        loadJson("api/account-reviews").catch(function () {
          return { recognizedSignals: [] };
        })
      ]);
    })
    .then(function (loaded) {
      editor.available = loaded[0].editable === true;
      // A server that advertises its own batch cap decides how many ids one
      // bank-review request may carry; an older one keeps the documented default.
      var batchLimit = Math.floor(Number(loaded[0].accountReviewBatch));
      editor.accountReviewBatchLimit = batchLimit > 0
        ? batchLimit : ACCOUNT_REVIEW_BATCH_LIMIT;
      accountReviewedSignals = {};
      (loaded[1].recognizedSignals || []).forEach(function (signal) {
        if (signal && typeof signal.id === "string" && Array.isArray(signal.checks)) {
          accountReviewedSignals[signal.id] = signal.checks.slice();
        }
      });
      refreshAccountAnalysis();
    })
    .then(function () {
      state.month = data.months[data.months.length - 1];
      var insuranceYears = data.transactions.filter(function (t) {
        return t.category === "Insurance";
      }).map(function (t) { return (t.date || t.month).slice(0, 4); }).sort();
      state.insuranceYear = insuranceYears.length
        ? insuranceYears[insuranceYears.length - 1] : null;
      state.period.year = state.month.slice(0, 4);
      state.period.month = state.month.slice(5);
      buildControls();
      renderAll();
      var gaps = (data.months.length && account.months.length)
        ? data.months.filter(function (m) { return account.months.indexOf(m) === -1; }).length : 0;
      document.getElementById("meta-foot").textContent =
        data.transactions.length + " card transactions · " + account.transactions.length +
        " account transactions · " + data.months.length + " months" +
        (gaps ? " (" + gaps + " without a UOB ONE statement)" : "") +
        " · generated " + data.generatedAt;
    })
    .catch(function (err) {
      var main = document.querySelector("main");
      main.textContent = "";
      var notice = document.createElement("p");
      notice.className = "empty";
      notice.textContent = "Could not load data (" + err.message +
        "). Run scripts/build_data.py and scripts/parse_one.py, then serve this folder over HTTP.";
      main.appendChild(notice);
    });
})();
