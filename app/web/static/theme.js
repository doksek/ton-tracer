/* Тема выставляется до отрисовки страницы, чтобы не было вспышки светлого фона. */
(function () {
  var saved = null;
  try { saved = localStorage.getItem("tt-theme"); } catch (e) { /* хранилище недоступно — не страшно */ }
  var dark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "dark" : "light";
})();
