// The guardrails of a chatbot (HIG-151, RF27, the screen 09 of the canvas): the base of the platform, always on and with
// no control to turn it off, and the topics of its own, each with what happens to a message on it (block with its fixed
// reply, or only log), and how many messages ran into each in the last 7 days, without their text.
//
// Only the cliente-admin adds, changes or deletes a topic; the cliente-analista sees them. Every change is a version of
// the chatbot (RF26), and hive-api tells the engine's classifier at once.
import { api, curl, explain } from "./api.js";

const { useState, useEffect, useMemo } = window.React;
const html = window.htm.bind(window.React.createElement);
const { Badge, Button, CodeSnippet, DataTable, EmptyState, Field, LoadingState, SlideOver } = window.Hive;

// The limits of hive-api (src/classes/dto_guardrail.py), said before sending.
const LIMITS = { topic: 100, description: 500, reply: 500 };
const ACTIONS = {
  block: { label: "Bloquear", tone: "error", help: "La persona recibe la respuesta fija y el mensaje no llega al modelo." },
  log: { label: "Registrar", tone: "info", help: "Se responde como siempre; queda registrado con la respuesta." },
};
const WEEK = 7 * 86400000;

function refused(answer) {
  if (answer.status === 409) {
    return /TOO_MANY_GUARDRAILS/.test(answer.message || "") ? "Un bot tiene 20 temas propios como mucho."
      : "El tema ya dice eso.";
  }
  if (answer.status === 422) {
    return "Revisa los campos: sin saltos de línea en el nombre, sin caracteres de control ni las marcas con las que "
      + "un modelo separa roles (<|…|>, [INST], <<SYS>>, <s>). Un tema que bloquea lleva su respuesta.";
  }
  return explain(answer);
}

function TopicEditor({ botId, topic, version, onClose, onSaved }) {
  const creating = !topic;
  const [form, setForm] = useState(topic ? { topic: topic.topic, description: topic.description, action: topic.action,
    reply: topic.reply || "" } : { topic: "", description: "", action: "block", reply: "" });
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const route = creating ? `chat/${botId}/guardrails` : `chat/${botId}/guardrails/${topic.uuid}`;
  const body = { topic: form.topic, description: form.description, action: form.action,
    ...(form.action === "block" ? { reply: form.reply } : {}) };
  const set = (field) => (event) => setForm({ ...form, [field]: event.target.value });
  const tooLong = Object.keys(LIMITS).find((field) => form[field].length > LIMITS[field]);
  const missing = !form.topic.trim() || !form.description.trim() || (form.action === "block" && !form.reply.trim());

  async function send(method) {
    setSaving(true);
    setError(null);
    const answer = await api(method, route, method === "DELETE" ? undefined : body);
    setSaving(false);
    if (answer.status === 200) onSaved();
    else setError(refused(answer));
  }

  return html`
    <${SlideOver} title=${creating ? "Añadir tema" : `Editar tema ${topic.topic}`}
        subtitle=${creating ? `POST /chat/${botId}/guardrails` : `PUT /chat/${botId}/guardrails/${topic.uuid}`}
        onClose=${onClose}
        footer=${html`
          <${Button} variant="ghost" onClick=${onClose}>Cancelar<//>
          <${Button} variant="primary" icon="check" disabled=${missing || Boolean(tooLong) || saving}
              onClick=${() => send(creating ? "POST" : "PUT")}>Guardar tema · v${(version || 0) + 1}<//>`}>
      <div class="pwa-form">
        <${Field} id="guardrail-topic" label="Tema" required value=${form.topic} onChange=${set("topic")}
            error=${form.topic.length > LIMITS.topic ? `Como mucho ${LIMITS.topic} caracteres.` : undefined}
            help="Un nombre corto, en una línea." />
        <${Field} id="guardrail-description" label="Qué abarca" multiline rows=${3} required value=${form.description}
            onChange=${set("description")}
            error=${form.description.length > LIMITS.description ? `Como mucho ${LIMITS.description} caracteres.` : undefined}
            help="El clasificador lo lee como la definición del tema, no como una instrucción." />
        <div>
          <label class="pwa-label" for="guardrail-action">Ante un mensaje sobre este tema</label>
          <select id="guardrail-action" class="pwa-select" value=${form.action} onChange=${set("action")}>
            ${Object.entries(ACTIONS).map(([value, action]) => html`<option key=${value} value=${value}>${action.label}</option>`)}
          </select>
          <span class="pwa-muted">${ACTIONS[form.action].help}</span>
        </div>
        ${form.action === "block" && html`
          <${Field} id="guardrail-reply" label="Respuesta fija" multiline rows=${3} required value=${form.reply}
              onChange=${set("reply")}
              error=${form.reply.length > LIMITS.reply ? `Como mucho ${LIMITS.reply} caracteres.` : undefined}
              help="Lo que recibe la persona en lugar de una respuesta del bot, tal como lo escribes." />`}
        ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      </div>
      ${!creating && html`<div class="pwa-divided">
        ${confirming ? html`
          <p class="pwa-notice">El clasificador deja de vigilarlo. Se recupera volviendo a la versión anterior.</p>
          <span class="pwa-row-actions">
            <${Button} variant="ghost" size="sm" onClick=${() => setConfirming(false)}>No borrar<//>
            <${Button} variant="danger" size="sm" icon="trash" disabled=${saving} onClick=${() => send("DELETE")}>Borrar el tema<//>
          </span>` : html`<${Button} variant="danger-outline" size="sm" icon="trash" onClick=${() => setConfirming(true)}>Borrar tema<//>`}
      </div>`}
      <${CodeSnippet} title=${creating ? "Añadir el tema" : "Editar el tema"}
          variants=${[{ lang: "bash", label: "cURL", code: curl(creating ? "POST" : "PUT", route, body) }]} />
    <//>`;
}

function Platform({ base }) {
  return html`
    <div class="pwa-card pwa-card-table">
      <div class="pwa-card-head">
        <div>
          <h2 class="pwa-subtitle">Base de la plataforma</h2>
          <span class="pwa-muted">Se aplica a todos los bots y no se desactiva. Si el filtro no puede juzgar un mensaje,
            el bot no lo responde.</span>
        </div>
        <${Badge} tone="muted" dot=${false}>🔒 Siempre<//>
      </div>
      <${DataTable} rows=${base.map((each) => ({ ...each, id: each.key }))} columns=${[
        { key: "topic", label: "Qué para", width: "200px" },
        { key: "description", label: "Qué abarca" },
        { key: "always", label: "", align: "right", width: "110px",
          render: () => html`<${Badge} tone="error">Bloquea<//>` }]} />
    </div>`;
}

export function Guardrails({ botId, admin, version, onSaved }) {
  const [seen, setSeen] = useState(null);
  const [week, setWeek] = useState(null);
  const [editing, setEditing] = useState(undefined);
  const [error, setError] = useState(null);

  async function load() {
    const since = new Date(Date.now() - WEEK).toISOString();
    const [guardrails, compliance] = await Promise.all([api("GET", `chat/${botId}/guardrails`),
      api("GET", `chat/${botId}/compliance?since=${encodeURIComponent(since)}`)]);
    if (guardrails.status === 200) setSeen(guardrails.data);
    else { setError(explain(guardrails)); setSeen({ platform: [], topics: [], limit: 20 }); }
    setWeek(compliance.status === 200 ? compliance.data.guardrails : null);
  }

  useEffect(() => { setSeen(null); load(); }, [botId, version]);

  function saved() {
    setEditing(undefined);
    load();
    // The card reads the chatbot again: its version went up.
    onSaved();
  }

  const hits = (week && week.topics) || {};
  const columns = useMemo(() => [
    { key: "topic", label: "Tema", width: "180px" },
    { key: "description", label: "Qué abarca" },
    { key: "action", label: "Acción", width: "120px",
      render: (row) => html`<${Badge} tone=${ACTIONS[row.action].tone}>${ACTIONS[row.action].label}<//>` },
    { key: "reply", label: "Respuesta fija", render: (row) => row.reply || html`<span class="pwa-muted">La de siempre</span>` },
    { key: "week", label: "7 días", align: "right", width: "80px", mono: true,
      render: (row) => { const counted = hits[row.uuid]; return counted ? String(counted.block + counted.log) : "0"; } },
    ...(admin ? [{ key: "act", label: "", align: "right", width: "80px",
      render: (row) => html`<${Button} variant="ghost" size="sm" onClick=${() => setEditing(row)}>Editar<//>` }] : []),
  ], [admin, week]);

  if (seen === null) return html`<${LoadingState} kind="rows" message="Leyendo los guardrails del bot…" />`;
  const platform = week ? Object.values(week.platform || {}).reduce((sum, n) => sum + n, 0) : null;
  return html`
    <section class="pwa-stack" aria-label="Guardrails">
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      <div class="pwa-two pwa-two-wide">
        <${Platform} base=${seen.platform} />
        <aside class="pwa-aside">
          <div class="pwa-card">
            <span class="pwa-eyebrow">Últimos 7 días</span>
            ${week ? html`
              <span>${week.blocked} ${week.blocked === 1 ? "mensaje bloqueado" : "mensajes bloqueados"} ·
                ${week.logged} ${week.logged === 1 ? "registrado" : "registrados"}</span>
              <span class="pwa-muted">${platform} por la base de la plataforma, el resto por los temas propios. Sin el
                texto de ningún mensaje.</span>`
              : html`<span class="pwa-muted">No se pudieron contar.</span>`}
          </div>
          <div class="pwa-card">
            <span class="pwa-eyebrow">Desde una integración</span>
            <${CodeSnippet} title="Los guardrails del bot" variants=${[{ lang: "bash", label: "cURL",
              code: curl("GET", `chat/${botId}/guardrails`) }]} />
            <span class="pwa-muted">${admin ? "Cada cambio crea una versión del bot y el motor la lee al momento."
              : "Tu rol los ve; solo el cliente-admin los cambia."}</span>
          </div>
        </aside>
      </div>
      <div class="pwa-card pwa-card-table">
        <div class="pwa-card-head">
          <div>
            <h2 class="pwa-subtitle">Temas propios</h2>
            <span class="pwa-muted">Por encima de la base: no la cambian. ${seen.topics.length} de ${seen.limit}.</span>
          </div>
          ${admin && seen.topics.length < seen.limit && html`<${Button} variant="secondary" size="sm" icon="plus"
              onClick=${() => setEditing(null)}>Añadir tema<//>`}
        </div>
        ${seen.topics.length === 0 ? html`<${EmptyState} title="Este bot no tiene temas propios">Solo lo vigila la
            base de la plataforma.${admin ? " Añade uno para bloquear o registrar los mensajes sobre un tema." : ""}<//>`
          : html`<${DataTable} columns=${columns} rows=${seen.topics.map((topic) => ({ ...topic, id: topic.uuid }))} />`}
      </div>
      ${editing !== undefined && html`<${TopicEditor} botId=${botId} topic=${editing} version=${version}
          onClose=${() => setEditing(undefined)} onSaved=${saved} />`}
    </section>`;
}
