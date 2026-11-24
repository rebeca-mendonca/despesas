(function () {
  var chave = "rolagem";
  try {
    var y = sessionStorage.getItem(chave);
    if (y !== null) {
      window.scrollTo(0, parseInt(y, 10) || 0);
      sessionStorage.removeItem(chave);
    }
  } catch (e) {}
  document.addEventListener("submit", function (ev) {
    if (ev.target.method && ev.target.method.toLowerCase() === "post") {
      try { sessionStorage.setItem(chave, String(window.scrollY)); } catch (e) {}
    }
  });
})();