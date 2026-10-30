// The PWA's way to hive-api: through the portal, at /api/<route>, with the cookie of the session and nothing else.
// It never holds a token: the portal does (HIG-93). A request that changes something carries the PWA's own header,
// without which the portal forwards nothing (CSRF).

export const PORTAL_HEADER = "X-Hive-Portal";

// Where to sign in again, coming back to the same screen.
export function signIn() {
  const back = window.location.pathname + window.location.hash;
  window.location.assign(`/auth/login?next=${encodeURIComponent(back)}`);
}

export async function session() {
  const answer = await fetch("/auth/session", { credentials: "same-origin" });
  return answer.status === 200 ? answer.json() : null;
}

export async function signOut() {
  const answer = await fetch("/auth/logout", {
    method: "POST", credentials: "same-origin", headers: { [PORTAL_HEADER]: "1" },
  });
  const data = await answer.json().catch(() => ({}));
  window.location.assign(data.logout || "/");
}

// A request to hive-api: its status and what it answered. A session that ended leads back to the login.
export async function api(method, route, body, extraHeaders = {}) {
  const headers = { Accept: "application/json", ...extraHeaders };
  if (method !== "GET") headers[PORTAL_HEADER] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const answer = await fetch(`/api/${route}`, {
    method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (answer.status === 401) {
    signIn();
    throw new Error("signed out");
  }
  const data = await answer.json().catch(() => null);
  return { status: answer.status, data: data && "data" in data ? data.data : data, message: data && data.message };
}

// What hive-api says when it says no, in words for the person: its message starts with a code of its own.
const CODES = {
  NO_WABA: "El bot no tiene cargada su cuenta de WhatsApp Business (wabaId).",
  NO_TOKEN: "El bot no tiene cargado su token de WhatsApp.",
  META_PERMISSION: "Meta no acepta el token del bot para gestionar plantillas: necesita el permiso "
    + "whatsapp_business_management.",
  META_REFUSED: "Meta no lo aceptó.",
  OUTSIDE_WINDOW: "La ventana de 24 horas está cerrada: solo se puede enviar una plantilla aprobada.",
  NO_CONSENT: "Fuera de la ventana, el contacto tiene que haber dado su consentimiento para esa categoría.",
  NO_WHATSAPP: "El bot no tiene un número de WhatsApp desde el que enviar.",
  TEMPLATE_NOT_APPROVED: "La plantilla no está aprobada, o no es de una categoría que se pueda enviar.",
  TEMPLATE_PARAMETERS: "La plantilla pide otro número de valores.",
  IDEMPOTENCY_KEY: "El envío no lleva una clave de idempotencia válida.",
  IDEMPOTENCY_KEY_REUSED: "Ese envío ya se pidió con otro contenido.",
};

export function explain(answer) {
  if (answer.status === 429) return "Demasiados envíos seguidos: espera un momento e inténtalo otra vez.";
  if (answer.status === 503) return "El servicio no está disponible ahora mismo: inténtalo en unos minutos.";
  if (answer.status === 403) return "Tu rol no permite esta acción.";
  const message = typeof answer.message === "string" ? answer.message : "";
  const code = message.split(":")[0];
  if (CODES[code]) return code === "META_REFUSED" ? `${CODES[code]} ${message.slice(code.length + 1).trim()}` : CODES[code];
  if (answer.status === 422) return "Revisa los campos: hay alguno que no es válido.";
  return message || `La API respondió ${answer.status}.`;
}

// The same request, as an integration would make it (the code-first pattern of ADR-0027): DPoP, never a cookie.
export function curl(method, route, body) {
  const url = `https://hive-api.higerotech.com/${route}`;
  const lines = [`curl -X ${method} '${url}' \\`, "  -H 'Authorization: DPoP <token de acceso>' \\",
    `  -H 'DPoP: <prueba para ${method} ${url.split("?")[0]}>'`];
  if (body !== undefined) {
    lines[lines.length - 1] += " \\";
    lines.push("  -H 'Content-Type: application/json' \\", `  -d '${JSON.stringify(body, null, 2)}'`);
  }
  return lines.join("\n");
}
