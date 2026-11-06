// Registers the service worker (installable app + offline app shell). Needs HTTPS or localhost.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
