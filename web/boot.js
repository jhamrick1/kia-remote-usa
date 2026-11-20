// Plain (non-module) watchdog. If the app's code never starts (a file failed to load while the PC
// or tunnel was briefly down), nothing else is left to retry, and the page sits on "Connecting"
// until reloaded by hand. So reload by itself, at most every 20 s, and offer a tap to retry.
(function () {
  var KEY = 'sportage.bootreload';
  function retry() {
    try { sessionStorage.setItem(KEY, String(Date.now())); } catch (e) { /* private mode */ }
    window.location.reload();
  }
  setTimeout(function () {
    if (window.__sportageBooted) return;
    var last = 0;
    try { last = Number(sessionStorage.getItem(KEY)) || 0; } catch (e) { /* ignore */ }
    if (Date.now() - last > 20000 && navigator.onLine !== false) { retry(); return; }
    var boot = document.getElementById('boot');
    if (boot) {
      boot.style.cursor = 'pointer';
      var label = boot.querySelector('[data-t]');
      if (label) label.textContent = 'Couldn’t reach the car service. Tap to try again.';
      boot.addEventListener('click', retry);
    }
    setTimeout(function () { if (!window.__sportageBooted) retry(); }, 20000);
  }, 8000);
})();
