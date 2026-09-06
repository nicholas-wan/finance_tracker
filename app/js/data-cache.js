(function () {
  "use strict";
  // One in-memory copy of each data file for every module on the page.
  // app.js, home.js and networth.js all need the statement rows; without this
  // each fetched its own multi-megabyte copy. Keyed by path without query, so
  // a cache-busting "?updated=" from one caller still shares the result.
  var promises = {};
  function key(url) { return String(url).split("?")[0]; }
  function load(url) {
    var k = key(url);
    if (!promises[k]) {
      promises[k] = fetch(url, { cache: "no-store" }).then(function (r) {
        if (!r.ok) throw new Error(url + " -> HTTP " + r.status);
        return r.json();
      });
      promises[k].catch(function () { delete promises[k]; });
    }
    return promises[k];
  }
  function refresh(url) { delete promises[key(url)]; return load(url); }
  window.FinanceData = { load: load, refresh: refresh };
}());
