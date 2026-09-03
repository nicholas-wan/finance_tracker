(function () {
  "use strict";

  var clientId = window.crypto && window.crypto.randomUUID
    ? window.crypto.randomUUID()
    : "00000000-0000-4000-8000-" + Math.random().toString(16).slice(2, 14).padEnd(12, "0");
  var timer = null;

  function send(path, beacon) {
    var body = JSON.stringify({ clientId: clientId });
    if (beacon && navigator.sendBeacon) {
      navigator.sendBeacon(path, new Blob([body], { type: "application/json" }));
      return;
    }
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      keepalive: true
    }).catch(function () {});
  }

  function heartbeat() {
    send("api/client-heartbeat", false);
  }

  heartbeat();
  timer = window.setInterval(heartbeat, 5000);
  window.addEventListener("pagehide", function () {
    if (timer) window.clearInterval(timer);
    send("api/client-disconnect", true);
  });
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      heartbeat();
      timer = window.setInterval(heartbeat, 5000);
    }
  });
}());
