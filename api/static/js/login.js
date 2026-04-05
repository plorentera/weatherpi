(function () {
  const params = new URLSearchParams(window.location.search);
  const next = params.get("next") || "/";
  const error = params.get("error");

  document.getElementById("nextPath").value = next;

  if (error === "1") {
    document.getElementById("loginError").classList.remove("d-none");
  }
})();
