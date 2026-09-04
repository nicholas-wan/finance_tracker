// Month-by-month diverging chart. Above the axis, income and outflows sit side
// by side; below it, money moved into investments. The legend doubles as a
// filter - click a series to drop it and rescale what is left.
window.Charts = (function () {
  "use strict";

  var NS = "http://www.w3.org/2000/svg";
  var MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // Colors resolve through CSS custom properties so dark mode supplies its own
  // validated steps instead of reusing the light hexes.
  var SERIES = [
    { key: "income", label: "Income", color: "var(--series-income)", side: "up" },
    // "Outflows", not "Spending": the series counts every non-wealth withdrawal
    // from the account, so it includes card-bill payments and transfers to your
    // own accounts - movements of money rather than spending.
    { key: "spent", label: "Outflows", color: "var(--series-spent)", side: "up" },
    { key: "invested", label: "Invested", color: "var(--series-invested)", side: "down" }
  ];

  function node(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    return n;
  }
  function label(x, y, str, opts) {
    opts = opts || {};
    var t = node("text", {
      x: x, y: y, "font-size": opts.size || 11, fill: opts.fill || "var(--text-3)"
    });
    if (opts.anchor) t.setAttribute("text-anchor", opts.anchor);
    t.textContent = str;
    return t;
  }
  function money0(n) {
    return (n < 0 ? "-" : "") + "S$" + Math.abs(Math.round(n)).toLocaleString("en-SG");
  }
  function compact(n) {
    var a = Math.abs(n);
    if (a >= 1000) return (a / 1000).toFixed(a >= 10000 ? 0 : 1) + "k";
    return String(Math.round(a));
  }
  function niceStep(max) {
    var raw = max / 3;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / mag;
    var step = norm >= 5 ? 5 : norm >= 2 ? 2 : 1;
    return step * mag;
  }
  function divergingMonths(container, rows, opts) {
    container.textContent = "";
    var visible = opts.visible || {};
    function on(key) { return visible[key] !== false; }

    if (!rows.length) {
      var p = document.createElement("p");
      p.className = "empty";
      p.textContent = "No statement data to chart";
      container.appendChild(p);
      buildLegend(container, visible, opts.onToggle);
      return;
    }

    var narrow = window.matchMedia && window.matchMedia("(max-width: 760px)").matches;
    var H = 246, plotTop = 18, plotBottom = 178, axisL = 40;
    // On phones the chart fills the panel when every month can keep a bar
    // pair at least 36px wide (six months on any phone); longer ranges keep
    // each month legible and scroll sideways instead, latest months first.
    var containerW = Math.max(container.clientWidth || 0, 280);
    var W = narrow ? Math.max(containerW, 36 * rows.length + axisL + 12) : 680;
    var slot = (W - axisL - 12) / rows.length;
    var labelStep = slot >= 30 ? 1 : slot >= 18 ? 2 : slot >= 12 ? 3 : 6;
    var pairW = Math.max(2, Math.min(26, (slot - 10) / 2));

    // One linear scale for every bar. A bonus month towers over the rest,
    // and that is the point: the chart stays to scale rather than clipping it.
    var maxUp = 0, maxDown = 0;
    rows.forEach(function (r) {
      if (on("income")) maxUp = Math.max(maxUp, r.income);
      if (on("spent")) maxUp = Math.max(maxUp, r.spent);
      if (on("invested")) maxDown = Math.max(maxDown, r.invested);
    });
    var displayMaxUp = maxUp;
    var span = displayMaxUp + maxDown;
    if (span <= 0) { displayMaxUp = 1; span = 1; }

    var plotH = plotBottom - plotTop;
    var axisY = plotTop + (displayMaxUp / span) * plotH;
    var scale = plotH / span;

    var s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 " + W + " " + H);
    s.setAttribute("width", "100%");
    if (narrow && W > containerW) s.style.minWidth = W + "px";
    s.setAttribute("role", "img");
    s.style.display = "block";
    s.setAttribute("aria-label",
      "Monthly chart with income and outflows above the axis and investments below");

    var step = niceStep(Math.max(displayMaxUp, maxDown) || 1);
    for (var v = step; v <= displayMaxUp + 0.01; v += step) {
      s.appendChild(node("line", {
        x1: axisL, y1: axisY - v * scale, x2: W - 12, y2: axisY - v * scale,
        stroke: "var(--border)", "stroke-width": 1, opacity: 0.55
      }));
      s.appendChild(label(axisL - 7, axisY - v * scale + 4, compact(v), { anchor: "end" }));
    }
    for (var d = step; d <= maxDown + 0.01; d += step) {
      s.appendChild(node("line", {
        x1: axisL, y1: axisY + d * scale, x2: W - 12, y2: axisY + d * scale,
        stroke: "var(--border)", "stroke-width": 1, opacity: 0.55
      }));
      s.appendChild(label(axisL - 7, axisY + d * scale + 4, compact(d), { anchor: "end" }));
    }
    s.appendChild(label(axisL - 7, axisY + 4, "0", { anchor: "end" }));

    // Value labels crowd each other once bars get thin, so below that width they
    // stay hidden until the column is hovered. Phones have no hover, so there
    // the larger of income and outflows keeps its label: one number per month
    // fits, and the smaller bar is still readable against the gridlines.
    var denseLabels = slot < 40;
    var phoneCompact = narrow && slot < 52;

    rows.forEach(function (r, i) {
      var cx = axisL + slot * i + slot / 2;
      var col = node("g", { class: "m-col" });
      col.style.setProperty("--motion-index", i);
      var dominant = r.income >= r.spent ? "income" : "spent";

      col.appendChild(node("rect", {
        class: "hover-band", x: axisL + slot * i + 1, y: plotTop - 8,
        width: slot - 2, height: plotH + 8, rx: 5, fill: "var(--text-3)",
        opacity: r.month === opts.activeMonth ? 0.12 : 0
      }));

      function value(x, y, amount, anchor, series) {
        var t = label(x, y, compact(amount), { anchor: anchor || "middle", size: 10 });
        var hidden = phoneCompact
          ? (series === "income" || series === "spent") && series !== dominant
          : denseLabels;
        t.setAttribute("class", "val" + (hidden ? " dense" : ""));
        col.appendChild(t);
      }

      if (on("income") && r.income > 0) {
        var hI = r.income * scale;
        col.appendChild(node("rect", {
          class: "chart-bar chart-bar-up",
          x: cx - pairW - 1, y: axisY - hI, width: pairW, height: Math.max(1, hI),
          rx: 2, fill: "var(--series-income)"
        }));
        value(phoneCompact ? cx : cx - pairW / 2 - 1, axisY - hI - 4, r.income, "middle", "income");
      }
      if (on("spent") && r.spent > 0) {
        var hS = r.spent * scale;
        col.appendChild(node("rect", {
          class: "chart-bar chart-bar-up",
          x: cx + 1, y: axisY - hS, width: pairW, height: Math.max(1, hS),
          rx: 2, fill: "var(--series-spent)"
        }));
        value(phoneCompact ? cx : cx + pairW / 2 + 1, axisY - hS - 4, r.spent, "middle", "spent");
      }
      if (on("invested") && r.invested > 0) {
        var hV = r.invested * scale;
        col.appendChild(node("rect", {
          class: "chart-bar chart-bar-down",
          x: cx - pairW / 2, y: axisY, width: pairW, height: Math.max(1, hV),
          rx: 2, fill: "var(--series-invested)"
        }));
        // The deepest bar's label used to land on the month name below it.
        value(cx, Math.min(axisY + hV + 11, plotBottom + 8), r.invested, "middle", "invested");
      }

      // The transparent target provides both the detailed tooltip and a
      // keyboard-accessible month drilldown.
      var hit = node("rect", {
        x: axisL + slot * i, y: plotTop - 8, width: slot, height: plotH + 8, fill: "transparent"
      });
      if (opts.onMonth) {
        hit.setAttribute("role", "button");
        hit.setAttribute("tabindex", "0");
        hit.setAttribute("aria-label", "View transactions for " +
          MONTH_NAMES[parseInt(r.month.slice(5), 10) - 1] + " " + r.month.slice(0, 4));
        hit.style.cursor = "pointer";
        hit.addEventListener("click", function () { opts.onMonth(r.month); });
        hit.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            opts.onMonth(r.month);
          }
        });
      }
      var title = document.createElementNS(NS, "title");
      title.textContent = MONTH_NAMES[parseInt(r.month.slice(5), 10) - 1] + " " + r.month.slice(0, 4) +
        "\nIncome " + money0(r.income) +
        "\nOutflows " + money0(r.spent) +
        "\nInvested " + money0(r.invested);
      hit.appendChild(title);
      col.appendChild(hit);

      // Every month gets a label, but the ones between ticks stay hidden until
      // the column is hovered, so dense ranges do not turn into a smear.
      var monthLabel = label(cx, plotBottom + 20,
        MONTH_NAMES[parseInt(r.month.slice(5), 10) - 1], { anchor: "middle" });
      monthLabel.setAttribute("class", "m-label" + (i % labelStep === 0 ? "" : " thin"));
      col.appendChild(monthLabel);
      s.appendChild(col);

      if (r.month.slice(5) === "01" || i === 0) {
        s.appendChild(label(cx, plotBottom + 34, r.month.slice(0, 4), { anchor: "middle" }));
      }
    });

    s.appendChild(node("line", {
      x1: axisL, y1: axisY, x2: W - 12, y2: axisY,
      stroke: "var(--text-3)", "stroke-width": 1
    }));

    var chartScroll = document.createElement("div");
    chartScroll.className = "chart-scroll";
    chartScroll.appendChild(s);
    container.appendChild(chartScroll);
    buildLegend(container, visible, opts.onToggle);
    if (narrow) chartScroll.scrollLeft = chartScroll.scrollWidth;
  }

  function buildLegend(container, visible, onToggle) {
    var legend = document.createElement("div");
    legend.className = "legend";
    var shown = SERIES.filter(function (x) { return visible[x.key] !== false; });
    SERIES.forEach(function (item) {
      var isOn = visible[item.key] !== false;
      var alone = shown.length === 1 && shown[0].key === item.key;
      var b = document.createElement("button");
      b.className = "legend-item toggle" + (isOn ? "" : " off");
      b.setAttribute("aria-pressed", isOn ? "true" : "false");
      b.title = alone ? "Show all three again" : "Show only " + item.label.toLowerCase();
      var sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = isOn ? item.color : "transparent";
      sw.style.boxShadow = "inset 0 0 0 1.5px " + item.color;
      b.appendChild(sw);
      b.appendChild(document.createTextNode(item.label));
      b.addEventListener("click", function () { onToggle(item.key); });
      legend.appendChild(b);
    });
    container.appendChild(legend);

    var note = document.createElement("p");
    note.className = "note";
    note.textContent = "Above the line, what came in against what went out. " +
      "Outflows include card-bill payments and transfers to your own accounts; " +
      "investments are shown below the axis.";
    container.appendChild(note);
  }

  return { divergingMonths: divergingMonths, series: SERIES };
})();
