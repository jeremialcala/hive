// The placeholder of the PWA (HIG-87): whether there is a session, and the way out. It never sees a token: the
// portal keeps them, and the browser only holds its cookie.
"use strict";

async function sesion() {
  const estado = document.getElementById("estado");
  const respuesta = await fetch("/auth/session", { credentials: "same-origin" });
  if (respuesta.status !== 200) {
    estado.textContent = "No has iniciado sesión.";
    document.getElementById("entrar").hidden = false;
    return;
  }
  const datos = await respuesta.json();
  estado.textContent = `Sesión iniciada como ${datos.role}.`;
  document.getElementById("salir").hidden = false;
}

async function salir() {
  // The PWA's own header: without it, the portal changes nothing (CSRF).
  const respuesta = await fetch("/auth/logout", {
    method: "POST", credentials: "same-origin", headers: { "X-Hive-Portal": "1" },
  });
  const datos = await respuesta.json();
  window.location.assign(datos.logout || "/");
}

document.getElementById("salir").addEventListener("click", salir);
sesion();
