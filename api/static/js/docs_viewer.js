(async function () {
  const target = document.getElementById("docBody");
  try {
    const res = await fetch("/docs/API.md?raw=1", { cache: "no-store" });
    if (!res.ok) {
      target.textContent = "No se pudo cargar la documentacion (HTTP " + res.status + ").";
      return;
    }
    target.textContent = await res.text();
  } catch (_) {
    target.textContent = "No se pudo cargar la documentacion.";
  }
})();
