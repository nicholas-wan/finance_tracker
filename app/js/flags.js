// Small inline flags for destinations. Drawn here rather than loaded, so the
// dashboard stays offline and Windows, which has no emoji flags, shows them
// too. Each is a 24x16 simplification: the colours and the one shape that
// makes a flag recognisable at 18 pixels wide, nothing finer.
window.Flags = (function () {
  "use strict";

  function svg(body) {
    return '<svg viewBox="0 0 24 16" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">' +
      body + "</svg>";
  }
  function stripes(colours, vertical) {
    var size = (vertical ? 24 : 16) / colours.length;
    return colours.map(function (colour, index) {
      return vertical
        ? '<rect x="' + (index * size) + '" y="0" width="' + size + '" height="16" fill="' + colour + '"/>'
        : '<rect x="0" y="' + (index * size) + '" width="24" height="' + size + '" fill="' + colour + '"/>';
    }).join("");
  }
  function star(cx, cy, r, colour) {
    var points = [];
    for (var i = 0; i < 10; i += 1) {
      var radius = i % 2 === 0 ? r : r * 0.42;
      var angle = -Math.PI / 2 + i * Math.PI / 5;
      points.push((cx + radius * Math.cos(angle)).toFixed(2) + "," + (cy + radius * Math.sin(angle)).toFixed(2));
    }
    return '<polygon points="' + points.join(" ") + '" fill="' + colour + '"/>';
  }
  function dots(list, r, colour) {
    return list.map(function (p) {
      return '<circle cx="' + p[0] + '" cy="' + p[1] + '" r="' + r + '" fill="' + colour + '"/>';
    }).join("");
  }
  // The Union Flag reduced to its crosses; used whole and in cantons.
  function unionJack(x, y, w, h) {
    var s = 'translate(' + x + ' ' + y + ') scale(' + (w / 24) + ' ' + (h / 16) + ')';
    return '<g transform="' + s + '">' +
      '<rect width="24" height="16" fill="#1e3f8f"/>' +
      '<path d="M0 0L24 16M24 0L0 16" stroke="#fff" stroke-width="3"/>' +
      '<path d="M0 0L24 16M24 0L0 16" stroke="#c8102e" stroke-width="1"/>' +
      '<path d="M12 0V16M0 8H24" stroke="#fff" stroke-width="4.4"/>' +
      '<path d="M12 0V16M0 8H24" stroke="#c8102e" stroke-width="2.4"/>' +
      "</g>";
  }

  var FLAGS = {
    "China": svg('<rect width="24" height="16" fill="#de2910"/>' + star(4.5, 5, 3, "#ffde00") +
      dots([[9.4, 2], [11, 3.8], [11, 6.4], [9.4, 8.2]], 0.7, "#ffde00")),
    "Japan": svg('<rect width="24" height="16" fill="#fff"/><circle cx="12" cy="8" r="4.6" fill="#bc002d"/>'),
    "South Korea": svg('<rect width="24" height="16" fill="#fff"/>' +
      '<path d="M12 3.6a4.4 4.4 0 0 1 0 8.8z" fill="#0047a0"/>' +
      '<path d="M12 3.6a4.4 4.4 0 0 0 0 8.8z" fill="#cd2e3a"/>' +
      '<path d="M12 3.6a2.2 2.2 0 0 1 0 4.4a2.2 2.2 0 0 0 0 4.4" fill="#0047a0"/>' +
      '<path d="M12 3.6a2.2 2.2 0 0 0 0 4.4a2.2 2.2 0 0 1 0 4.4" fill="#cd2e3a"/>' +
      '<path d="M2.2 3.2l3.2-2.1M2.8 4.1l3.2-2.1M3.4 5l3.2-2.1M18.6 1.1l3.2 2.1M19.2 2l3.2 2.1M19.8 2.9l3.2 2.1M2.2 12.8l3.2 2.1M2.8 11.9l3.2 2.1M3.4 11l3.2 2.1M18.6 14.9l3.2-2.1M19.2 14l3.2-2.1M19.8 13.1l3.2-2.1" stroke="#000" stroke-width="0.7"/>'),
    "Taiwan": svg('<rect width="24" height="16" fill="#fe0000"/><rect width="12" height="8" fill="#000095"/>' +
      '<circle cx="6" cy="4" r="2.6" fill="#fff"/><circle cx="6" cy="4" r="1.8" fill="#000095"/><circle cx="6" cy="4" r="1.3" fill="#fff"/>'),
    "Hong Kong": svg('<rect width="24" height="16" fill="#de2910"/>' +
      [0, 72, 144, 216, 288].map(function (angle) {
        return '<ellipse cx="12" cy="4.9" rx="1.5" ry="3.1" fill="#fff" transform="rotate(' + angle + ' 12 8)"/>';
      }).join("")),
    "Macau": svg('<rect width="24" height="16" fill="#00785e"/>' +
      '<path d="M12 4.5c-1 1.6-1 3.2 0 4.6c1-1.4 1-3 0-4.6z" fill="#fff"/>' +
      '<path d="M9.6 6.2c0 1.6.6 2.8 2.4 3.4c-1.8.2-3-.8-3.4-2.6z" fill="#fff"/>' +
      '<path d="M14.4 6.2c0 1.6-.6 2.8-2.4 3.4c1.8.2 3-.8 3.4-2.6z" fill="#fff"/>' +
      '<path d="M8 11h8M8.8 12.6h6.4" stroke="#fff" stroke-width="0.8"/>' + star(12, 2.6, 1.1, "#fbd116")),
    "Singapore": svg(stripes(["#ef3340", "#fff"]) +
      '<circle cx="5.2" cy="4" r="2.6" fill="#fff"/><circle cx="6.3" cy="4" r="2.4" fill="#ef3340"/>' +
      dots([[7.6, 2.2], [9.2, 3.2], [8.8, 5.2], [6.6, 5.2], [6.1, 3.2]], 0.45, "#fff")),
    "Malaysia": svg(stripes(["#cc0001", "#fff", "#cc0001", "#fff", "#cc0001", "#fff", "#cc0001", "#fff"]) +
      '<rect width="12" height="8" fill="#010066"/>' +
      '<circle cx="5" cy="4" r="2.6" fill="#ffcc00"/><circle cx="6" cy="4" r="2.4" fill="#010066"/>' + star(8.6, 4, 1.6, "#ffcc00")),
    "Thailand": svg(stripes(["#a51931", "#fff", "#2d2a4a", "#2d2a4a", "#fff", "#a51931"])),
    "Vietnam": svg('<rect width="24" height="16" fill="#da251d"/>' + star(12, 8, 4.2, "#ffff00")),
    "Indonesia": svg(stripes(["#e70011", "#fff"])),
    "Philippines": svg(stripes(["#0038a8", "#ce1126"]) +
      '<path d="M0 0L9 8L0 16z" fill="#fff"/><circle cx="3.2" cy="8" r="1.5" fill="#fcd116"/>'),
    "India": svg(stripes(["#ff9933", "#fff", "#138808"]) +
      '<circle cx="12" cy="8" r="1.9" fill="none" stroke="#000080" stroke-width="0.7"/>'),
    "Australia": svg('<rect width="24" height="16" fill="#00008b"/>' + unionJack(0, 0, 12, 8) +
      star(6, 12.2, 2, "#fff") + dots([[18, 2.5], [21.5, 6], [18, 13], [15, 8.5], [19.5, 8.5]], 0.75, "#fff")),
    "New Zealand": svg('<rect width="24" height="16" fill="#00247d"/>' + unionJack(0, 0, 12, 8) +
      dots([[18, 3], [21, 7], [15.5, 7.5], [18.5, 12]], 1.05, "#fff") +
      dots([[18, 3], [21, 7], [15.5, 7.5], [18.5, 12]], 0.7, "#cc142b")),
    "Canada": svg('<rect width="24" height="16" fill="#fff"/><rect width="6" height="16" fill="#d52b1e"/>' +
      '<rect x="18" width="6" height="16" fill="#d52b1e"/>' +
      '<polygon points="12,2.4 12.9,4.6 14.6,3.9 14.1,6 16.2,5.8 14.8,7.6 16.2,9.1 14.1,8.9 14.4,11 12.6,9.9 12,12.6 11.4,9.9 9.6,11 9.9,8.9 7.8,9.1 9.2,7.6 7.8,5.8 9.9,6 9.4,3.9 11.1,4.6" fill="#d52b1e"/>'),
    "United States": svg(stripes(["#b22234", "#fff", "#b22234", "#fff", "#b22234", "#fff", "#b22234"]) +
      '<rect width="10" height="8.6" fill="#3c3b6e"/>' +
      dots([[1.6, 1.6], [4, 1.6], [6.4, 1.6], [8.8, 1.6], [2.8, 3.4], [5.2, 3.4], [7.6, 3.4],
        [1.6, 5.2], [4, 5.2], [6.4, 5.2], [8.8, 5.2], [2.8, 7], [5.2, 7], [7.6, 7]], 0.5, "#fff")),
    "United Kingdom": svg(unionJack(0, 0, 24, 16)),
    "France": svg(stripes(["#0055a4", "#fff", "#ef4135"], true)),
    "Germany": svg(stripes(["#000", "#dd0000", "#ffce00"])),
    "Switzerland": svg('<rect width="24" height="16" fill="#d52b1e"/><path d="M12 3.5v9M7.5 8h9" stroke="#fff" stroke-width="2.6"/>'),
    "Unknown": svg('<rect width="24" height="16" fill="#8a8a8a"/>' +
      '<circle cx="12" cy="8" r="4.6" fill="none" stroke="#fff" stroke-width="1.1"/>' +
      '<path d="M7.4 8h9.2M12 3.4c-2.4 2.6-2.4 6.6 0 9.2M12 3.4c2.4 2.6 2.4 6.6 0 9.2" fill="none" stroke="#fff" stroke-width="0.9"/>')
  };

  function has(country) { return Object.prototype.hasOwnProperty.call(FLAGS, country); }
  function markup(country) { return FLAGS[has(country) ? country : "Unknown"]; }
  // A flag chip element; the country name is expected beside it, so the
  // image itself carries no accessible name.
  function node(country, size) {
    var chip = document.createElement("span");
    chip.className = "flag" + (size ? " flag-" + size : "");
    chip.innerHTML = markup(country);
    return chip;
  }

  return { has: has, markup: markup, node: node, countries: Object.keys(FLAGS) };
})();
