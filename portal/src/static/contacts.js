// The contacts of a chatbot, and sending to one (HIG-87): the conversation with the window of 24 hours, the message (a
// text, or an approved template with its values and its preview) and what came of it. A cliente-analista sends
// only approved templates; with the window closed, nobody sends anything else.
import { api, curl, explain } from "./api.js";
import { Preview, stateBadge, valuesIn } from "./templates.js";

const { useState, useEffect, useRef } = window.React;
const html = window.htm.bind(window.React.createElement);
const { Badge, Button, CodeSnippet, DataTable, EmptyState, Field, LoadingState, Tabs, Thread } = window.Hive;

// What came of a send, in words: the reason hive-api gives, as Meta's code says it (developers.facebook.com).
export const REASONS = {
  window_closed: "La ventana de 24 horas se cerró: a este contacto solo se le puede enviar una plantilla aprobada.",
  template_paused: "Meta pausó la plantilla por su calidad: no se puede enviar hasta que la reactive.",
  template_disabled: "Meta desactivó la plantilla.",
  template_parameters: "Los valores no coinciden con los que pide la plantilla.",
  template_missing: "La plantilla no existe en Meta con ese nombre e idioma.",
  marketing_limit: "Meta limita los mensajes de marketing que recibe cada persona: inténtalo dentro de 24 horas.",
  not_deliverable: "Meta no pudo entregar el mensaje: el número quizá no usa WhatsApp.",
  unsupported: "Meta no admite este tipo de mensaje para este contacto.",
  sample_template: "hello_world es la plantilla de muestra de Meta: solo sale desde sus números públicos de prueba. "
    + "Envía una plantilla propia aprobada.",
  no_token: "El bot no tiene token de WhatsApp: cárgalo antes de enviar.",
  gave_up: "No se pudo enviar después de varios intentos: inténtalo más tarde.",
  rejected: "Meta rechazó el mensaje.",
};
const POLL_EVERY = 2000; // milliseconds
const POLL_FOR = 90000;

function when(moment) {
  return moment ? new Date(moment).toLocaleString("es", { dateStyle: "short", timeStyle: "short" }) : "";
}

function left(closesAt) {
  const minutes = Math.max(0, Math.round((new Date(closesAt) - Date.now()) / 60000));
  return minutes >= 60 ? `${Math.floor(minutes / 60)} h ${minutes % 60} min` : `${minutes} min`;
}

export function Contacts({ botId, onOpen }) {
  const [contacts, setContacts] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api("GET", `chat/${botId}/contacts?limit=500`).then((answer) => {
      if (answer.status === 200) setContacts(answer.data.contacts);
      else { setError(explain(answer)); setContacts([]); }
    });
  }, [botId]);

  if (contacts === null) return html`<${LoadingState} kind="rows" message="Leyendo los contactos del bot…" />`;
  // The number is not shown: the conversation goes without it too.
  const rows = contacts.map((contact) => ({ id: contact.uuid,
    name: [contact.firstName, contact.lastName].filter(Boolean).join(" ") || "Sin nombre",
    channel: contact.waId ? "WhatsApp" : "Messenger", since: when(contact.createdAt) }));
  return html`
    <section aria-label="Contactos">
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      ${rows.length === 0 ? html`<${EmptyState} title="Nadie ha escrito todavía a este bot" />`
        : html`<${DataTable} rows=${rows} onRowClick=${(row) => onOpen(row.id)} columns=${[
          { key: "name", label: "Contacto" }, { key: "channel", label: "Canal" }, { key: "since", label: "Desde" }]} />`}
    </section>`;
}

// hive-api writes who sent it as «person <id>» or «key <kid>» (Principal.actor).
function sender(sentBy, person) {
  if (person && sentBy === `person ${person}`) return "Enviado por ti";
  if (sentBy && sentBy.startsWith("person ")) return "Enviado por una persona del cliente";
  if (sentBy && sentBy.startsWith("key ")) return "Enviado por la API del cliente";
  return "Enviado por el cliente";
}

function asThread(messages, person) {
  return messages.map((message, index) => ({
    id: message.messageId || String(index),
    role: message.direction === "in" || message.direction === "incoming" ? "customer"
      : message.origin === "client" ? "operator" : "bot",
    text: message.text || (message.attachments || []).join(", ") || "",
    time: when(message.sentAt),
    author: message.origin === "client" ? sender(message.sentBy, person) : undefined,
  }));
}

function Outcome({ status, onDone }) {
  if (!status) return null;
  if (status.phase === "bus") {
    return html`<${LoadingState} kind="spinner" message="Esperando confirmación del bus de eventos…" detail="SALIENTES" />`;
  }
  if (status.phase === "meta") {
    return html`<${LoadingState} kind="spinner" message="Bifröst lo tiene: esperando la respuesta de Meta…"
        detail=${`pedido ${status.uuid}`} />`;
  }
  if (status.phase === "sent") {
    return html`<p class="pwa-success" role="status">Enviado: Meta lo aceptó. Ya está en la conversación.
        <${Button} variant="ghost" size="sm" onClick=${onDone}>Cerrar<//></p>`;
  }
  if (status.phase === "slow") {
    return html`<p class="pwa-notice" role="status">Meta todavía no respondió. El envío sigue en curso: aparecerá en la
        conversación cuando salga. <${Button} variant="ghost" size="sm" onClick=${onDone}>Cerrar<//></p>`;
  }
  const code = status.failure && status.failure.code;
  return html`<p class="pwa-error" role="alert">
      ${status.text || REASONS[status.failure && status.failure.reason] || REASONS.rejected}
      ${code ? html` <span class="pwa-muted">(código de Meta ${code})</span>` : ""}
      <${Button} variant="ghost" size="sm" onClick=${onDone}>Cerrar<//></p>`;
}

function Sender({ botId, contactId, window: open, admin, onSent }) {
  const textAllowed = admin && open.open;
  const [mode, setMode] = useState(textAllowed ? "text" : "template");
  const [text, setText] = useState("");
  const [templates, setTemplates] = useState([]);
  const [chosen, setChosen] = useState("");
  const [values, setValues] = useState([]);
  const [status, setStatus] = useState(null);
  const polling = useRef(null);

  useEffect(() => {
    api("GET", `chat/${botId}/templates`).then((answer) => {
      if (answer.status === 200) setTemplates(answer.data.templates.filter((template) => template.status === "APPROVED"));
    });
    return () => clearTimeout(polling.current);
  }, [botId]);
  useEffect(() => { if (!textAllowed) setMode("template"); }, [textAllowed]);
  // What came of a send belongs to that message: a new one starts without it.
  useEffect(() => { if (status && !["bus", "meta"].includes(status.phase)) setStatus(null); }, [mode, chosen]);

  const template = templates.find((each) => `${each.name}:${each.language}` === chosen);
  const body = template ? ((template.components || []).find((part) => part.type === "BODY") || {}).text || "" : "";
  const route = `chat/${botId}/contacts/${contactId}/messages`;
  const message = mode === "text" ? { type: "text", text }
    : { type: "template", template: template ? { name: template.name, language: template.language,
      parameters: values.slice(0, template.parameters) } : undefined };
  const ready = mode === "text" ? text.trim().length > 0
    : Boolean(template) && values.slice(0, template.parameters).filter((value) => value && value.trim()).length
      === template.parameters;

  function follow(uuid, started) {
    polling.current = setTimeout(async () => {
      const answer = await api("GET", `${route}/${uuid}`);
      const data = answer.status === 200 ? answer.data : null;
      if (data && data.status === "sent") { setStatus({ phase: "sent" }); setText(""); setValues([]); onSent(); return; }
      if (data && data.status === "failed") { setStatus({ phase: "failed", failure: data.failure }); return; }
      if (Date.now() - started > POLL_FOR) { setStatus({ phase: "slow" }); return; }
      setStatus({ phase: "meta", uuid });
      follow(uuid, started);
    }, POLL_EVERY);
  }

  async function send() {
    setStatus({ phase: "bus" });
    const key = window.crypto.randomUUID();
    const answer = await api("POST", route, message, { "Idempotency-Key": key });
    if (answer.status !== 202) { setStatus({ phase: "failed", text: explain(answer) }); return; }
    setStatus({ phase: "meta", uuid: answer.data.uuid });
    follow(answer.data.uuid, Date.now());
  }

  const modes = [...(textAllowed ? [{ value: "text", label: "Texto" }] : []), { value: "template", label: "Plantilla" }];
  return html`
    <section class="pwa-sender" aria-label="Enviar un mensaje">
      <h3 class="pwa-subtitle">Enviar un mensaje</h3>
      ${!open.open && html`<p class="pwa-notice">La ventana de 24 horas está cerrada: solo se puede enviar una plantilla
          aprobada, y solo si el contacto dio su consentimiento para su categoría.</p>`}
      ${modes.length > 1 && html`<${Tabs} compact items=${modes} value=${mode} onChange=${setMode} />`}
      ${mode === "text" ? html`
        <${Field} id="send-text" label="Texto" multiline rows=${4} value=${text}
            onChange=${(event) => setText(event.target.value)} help="Hasta 4 096 caracteres, sin vista previa de enlaces." />`
        : html`
        <label class="pwa-label" for="send-template">Plantilla aprobada</label>
        <select id="send-template" class="pwa-select" value=${chosen}
            onChange=${(event) => { setChosen(event.target.value); setValues([]); }}>
          <option value="">${templates.length ? "Elige una plantilla" : "Este bot no tiene plantillas aprobadas"}</option>
          ${templates.map((each) => html`<option key=${`${each.name}:${each.language}`}
              value=${`${each.name}:${each.language}`}>${each.name} · ${each.language} · ${each.category}</option>`)}
        </select>
        ${template && valuesIn(body).map((number) => html`
          <${Field} key=${number} id=${`send-value-${number}`} label=${`Valor de {{${number}}}`} value=${values[number - 1] || ""}
              onChange=${(event) => { const next = [...values]; next[number - 1] = event.target.value; setValues(next); }} />`)}
        ${template && html`<${Preview} body=${body} values=${values}
            header=${((template.components || []).find((part) => part.type === "HEADER") || {}).text}
            footer=${((template.components || []).find((part) => part.type === "FOOTER") || {}).text}
            buttons=${((template.components || []).find((part) => part.type === "BUTTONS") || {}).buttons} />`}`}
      <div class="pwa-toolbar">
        <${Button} variant="primary" disabled=${!ready || (status && ["bus", "meta"].includes(status.phase))}
            onClick=${send}>Enviar<//>
      </div>
      <${Outcome} status=${status} onDone=${() => setStatus(null)} />
      <${CodeSnippet} title="Enviar este mensaje" variants=${[{ lang: "bash", label: "cURL",
        code: curl("POST", route, message).replace("-H 'Content-Type", "-H 'Idempotency-Key: <una clave por envío>' \\\n  -H 'Content-Type") }]} />
    </section>`;
}

export function Contact({ botId, contactId, admin, person, onBack }) {
  const [conversation, setConversation] = useState(null);
  const [error, setError] = useState(null);

  async function load() {
    const answer = await api("GET", `chat/${botId}/contacts/${contactId}/conversation`);
    if (answer.status === 200) setConversation(answer.data);
    else setError(explain(answer));
  }

  useEffect(() => { load(); }, [botId, contactId]);

  if (error) return html`<p class="pwa-error" role="alert">${error}</p>`;
  if (conversation === null) return html`<${LoadingState} kind="spinner" message="Leyendo la conversación…" />`;
  const { contact, window: open, messages } = conversation;
  const name = [contact.firstName, contact.lastName].filter(Boolean).join(" ") || "Sin nombre";
  const consent = contact.consent && !contact.consent.withdrawnAt ? contact.consent.categories.join(", ") : null;
  return html`
    <section aria-label=${`Contacto ${name}`}>
      <p><a href=${onBack}>← Contactos</a></p>
      <header class="pwa-contact-header">
        <h2 class="heading-md">${name}</h2>
        ${open.open ? html`<${Badge} tone="success" dot>Ventana abierta · quedan ${left(open.closesAt)}<//>`
          : html`<${Badge} tone="muted" dot>Ventana cerrada<//>`}
        <span class="pwa-muted">Consentimiento: ${consent || "ninguno"}</span>
      </header>
      ${messages.length ? html`<${Thread} ariaLabel="Conversación" ratable=${false} messages=${asThread(messages, person)} />`
        : html`<${EmptyState} title="Sin mensajes en el historial" />`}
      ${contact.channel === "whatsapp" ? html`<${Sender} botId=${botId} contactId=${contactId} window=${open} admin=${admin}
          onSent=${load} />` : html`<p class="pwa-notice">En la v1 solo se envía por WhatsApp.</p>`}
    </section>`;
}

export { stateBadge };
