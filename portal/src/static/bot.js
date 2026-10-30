// The card of a chatbot (HIG-121, the screens 05 to 08 of the canvas): its header with its state and version, and its
// tabs: Identidad (the persona, RF23), Credenciales de Meta (masked, only to load or replace), Cerebro e intents (its
// rules and intents, RF24), Versiones (RF26) and, as before, its Plantillas and Contactos.
//
// Every change goes to hive-api, which makes it a version and tells the engine. A cliente-analista sees all of it and
// changes nothing: the forms are read only and no button that would answer 403 is shown. hive-api decides.
import { api, curl, explain } from "./api.js";
import { Templates } from "./templates.js";
import { Contacts, Contact } from "./contacts.js";
import { PageCall, go } from "./shell.js";

const { useState, useEffect, useMemo } = window.React;
const html = window.htm.bind(window.React.createElement);
const { Badge, Button, Cell, ChannelChip, CodeSnippet, DataTable, EmptyState, Field, LoadingState, SlideOver, Tabs } =
  window.Hive;

export const TABS = [
  { value: "identidad", label: "Identidad" },
  { value: "credenciales", label: "Credenciales de Meta" },
  { value: "cerebro", label: "Cerebro e intents" },
  { value: "versiones", label: "Versiones" },
  { value: "plantillas", label: "Plantillas" },
  { value: "contactos", label: "Contactos" },
];

// The limits of RS11, as hive-api checks them (src/classes/bot_text.py): said before sending, so the person sees them.
const LIMITS = { name: 100, role: 100, tone: 100, description: 2000, rule: 1000 };
const PERSONA_FIELDS = [["name", "Nombre"], ["role", "Rol"], ["tone", "Tono"], ["description", "Descripción"]];
const WEBHOOK = "https://hive-webhook.higerotech.com/chat";

// What hive-api's 422 says of each field, in words (src/service.py answers type, loc and msg, never the value).
function fieldErrors(answer) {
  const details = answer.status === 422 && answer.data && Array.isArray(answer.data.detail) ? answer.data.detail : [];
  const errors = {};
  for (const detail of details) {
    const field = (detail.loc || []).filter((part) => part !== "body").join(".");
    errors[field || "_"] = detail.type === "string_too_long" ? "Es demasiado largo."
      : detail.type === "string_too_short" || detail.type === "missing" ? "No puede ir vacío."
        : /control|marks|blank|line break/i.test(detail.msg || "")
          ? "No puede llevar saltos de línea ni caracteres de control, ni las marcas con las que un modelo separa roles (<|…|>, [INST], <<SYS>>, <s>)."
          : /destination|Host/i.test(detail.msg || "") ? `Destino no permitido: ${detail.msg.replace(/^Value error, /, "")}`
            : "No es válido.";
  }
  return errors;
}

function tooLong(draft) {
  const problems = {};
  for (const [field, limit] of Object.entries(LIMITS)) {
    if (typeof draft[field] === "string" && draft[field].length > limit) problems[field] = `Como mucho ${limit} caracteres.`;
  }
  return problems;
}

export function who(author) {
  if (!author) return "";
  if (author === "hive") return "Hive";
  if (author.startsWith("person ")) return `persona ${author.slice(7, 15)}…`;
  if (author.startsWith("key ")) return "credencial de máquina";
  return author;
}

const CHANGES = {
  "chatbot created": "Bot creado", "rules added": "Reglas añadidas", "rule changed": "Regla editada",
  "rule deleted": "Regla borrada", "persona changed": "Identidad editada", "intent created": "Intent creado",
  "intent changed": "Intent editado", "intent deleted": "Intent borrado", restored: "Vuelta a una versión",
  "before versions": "Lo que tenía antes de las versiones",
};

function when(iso) {
  return iso ? iso.replace("T", " ").slice(0, 16) : "";
}

// --- The header -----------------------------------------------------------------------------------------------------

function BotHeader({ client, bot, version }) {
  const state = ["REG", "ACT", "ERR", "REV"].includes(bot.status) ? bot.status : "REG";
  return html`
    <nav class="pwa-crumbs" aria-label="Ruta"><a href="#/bots">Bots</a> / <span aria-current="page">${bot.name}</span></nav>
    <header class="pwa-bot-header">
      <${Cell} name=${bot.name} size=${56} tone=${state === "ERR" ? "error" : "amber"} />
      <div class="pwa-page-titles">
        <span class="pwa-eyebrow">${(client && client.name) || "Hive"}</span>
        <div class="pwa-bot-title">
          <h1 class="heading-lg">${bot.name}</h1>
          <${Badge} state=${state} />
          ${bot.credentials && bot.credentials.whatsappToken && html`<${ChannelChip} channel="whatsapp" />`}
        </div>
        <span class="mono pwa-muted">bot_id ${bot.uuid}${version ? ` · versión v${version}` : ""}</span>
      </div>
    </header>
    ${state === "ERR" && html`<p class="pwa-error" role="alert">
      El bot está en ERR: Meta rechazó su token o su App Secret. Cárgalos otra vez en Credenciales de Meta; vuelve a ACT
      con el primer envío o la primera firma que Meta acepte.</p>`}`;
}

// --- Identidad (RF23) -----------------------------------------------------------------------------------------------

function Identity({ botId, admin, config, onSaved }) {
  const persona = { name: config.name, role: config.role, tone: config.tone, description: config.description || "" };
  const [draft, setDraft] = useState(persona);
  const [errors, setErrors] = useState({});
  const [notice, setNotice] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => { setDraft(persona); setErrors({}); }, [config.version]);

  const changed = PERSONA_FIELDS.some(([field]) => draft[field] !== persona[field]);
  const problems = { ...tooLong(draft), ...errors };
  const set = (field) => (event) => {
    setDraft({ ...draft, [field]: event.target.value });
    setErrors({ ...errors, [field]: undefined });
  };

  async function save() {
    setSaving(true);
    setNotice(null);
    const answer = await api("PUT", `chat/${botId}/persona`, draft);
    setSaving(false);
    if (answer.status === 200) {
      setNotice({ ok: true, text: `Guardado como la versión v${answer.data.version}.` });
      onSaved();
    } else if (answer.status === 422) setErrors(fieldErrors(answer));
    else setNotice({ ok: false, text: answer.status === 409 ? "El bot ya tiene esta identidad." : explain(answer) });
  }

  return html`
    <section class="pwa-two" aria-label="Identidad">
      <form class="pwa-form" onSubmit=${(event) => { event.preventDefault(); if (admin) save(); }}>
        <${Field} id="persona-name" label="Nombre" required value=${draft.name} onChange=${set("name")} disabled=${!admin}
            help="Así lo ven las personas de tu cliente y así se presenta el bot." error=${problems.name} />
        <div class="pwa-pair">
          <${Field} id="persona-role" label="Rol" required value=${draft.role} onChange=${set("role")} disabled=${!admin}
              help="Una frase: quién es el bot." error=${problems.role} />
          <${Field} id="persona-tone" label="Tono" required value=${draft.tone} onChange=${set("tone")} disabled=${!admin}
              help="cercano · formal · directo" error=${problems.tone} />
        </div>
        <${Field} id="persona-description" label="Descripción" multiline rows=${5} value=${draft.description}
            onChange=${set("description")} disabled=${!admin} error=${problems.description}
            help=${`Hasta ${LIMITS.description} caracteres. Sin caracteres de control ni marcas de rol de un modelo (RS11).`} />
        ${notice && html`<p class=${notice.ok ? "pwa-success" : "pwa-error"} role=${notice.ok ? "status" : "alert"}>${notice.text}</p>`}
        ${admin && html`
          <div class="pwa-form-actions">
            <${Button} variant="ghost" disabled=${!changed || saving} onClick=${() => { setDraft(persona); setErrors({}); }}>Descartar<//>
            <span class="pwa-grow"></span>
            <span class="pwa-muted">Guardar crea la versión <span class="mono">v${(config.version || 0) + 1}</span></span>
            <${Button} variant="primary" icon="check" type="submit"
                disabled=${!changed || saving || Object.values(tooLong(draft)).length > 0}>Guardar cambios<//>
          </div>`}
      </form>
      <aside class="pwa-aside">
        <div class="pwa-card">
          <span class="pwa-eyebrow">Quién puede editar</span>
          <span><strong>cliente-admin</strong> edita y guarda. <strong>cliente-analista</strong> ve la identidad y las
            versiones, sin cambiarlas.</span>
        </div>
        <${PageCall} title="Cambiar la identidad" method="PUT" route=${`chat/${botId}/persona`} body=${draft} />
      </aside>
    </section>`;
}

// --- Credenciales de Meta -------------------------------------------------------------------------------------------

const SECRETS = [
  ["facebookToken", "facebookToken", "El token de la página, para Messenger e Instagram."],
  ["whatsappToken", "whatsappToken", "El token de WhatsApp Business, para enviar y para las plantillas."],
  ["appSecret", "appSecret", "El App Secret de la app de Meta: con él se comprueba la firma de cada notificación."],
];

function Credential({ botId, field, label, help, loaded, admin, onSaved }) {
  const [value, setValue] = useState("");
  const [replacing, setReplacing] = useState(!loaded);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    setError(null);
    const answer = await api("PUT", `chat/${botId}/credentials`, { [field]: value });
    setSaving(false);
    if (answer.status === 200) { setValue(""); setReplacing(false); onSaved(); }
    else setError(answer.status === 422 ? "No es un valor válido." : explain(answer));
  }

  return html`
    <div class="pwa-secret">
      <${Field} id=${`cred-${field}`} label=${label} secret saved=${loaded && !replacing} value=${value} help=${help}
          disabled=${!admin} error=${error} onChange=${(event) => setValue(event.target.value)} />
      ${admin && (loaded && !replacing
        ? html`<${Button} variant="secondary" onClick=${() => setReplacing(true)}>Reemplazar<//>`
        : html`<${Button} variant="primary" icon="lock" disabled=${!value || saving} onClick=${save}>Guardar<//>`)}
    </div>`;
}

function Credentials({ botId, admin, bot, onSaved }) {
  const loaded = bot.credentials || {};
  const [waba, setWaba] = useState("");
  const [wabaError, setWabaError] = useState(null);

  async function saveWaba() {
    const answer = await api("PUT", `chat/${botId}/credentials`, { wabaId: waba });
    if (answer.status === 200) { setWaba(""); setWabaError(null); onSaved(); }
    else setWabaError(answer.status === 422 ? "Son de 5 a 25 cifras." : explain(answer));
  }

  return html`
    <section class="pwa-two" aria-label="Credenciales de Meta">
      <div class="pwa-form">
        <div>
          <h2 class="pwa-subtitle">Tokens de Meta</h2>
          <p class="pwa-muted">Se guardan cifrados con la clave de tu cliente y no vuelven a mostrarse: solo se cargan o
            se reemplazan.</p>
        </div>
        ${SECRETS.map(([field, label, help]) => html`<${Credential} key=${field} botId=${botId} field=${field}
            label=${label} help=${help} loaded=${Boolean(loaded[field])} admin=${admin} onSaved=${onSaved} />`)}
        <div class="pwa-secret">
          <${Field} id="cred-wabaId" label="wabaId" mono value=${waba} disabled=${!admin} error=${wabaError}
              placeholder=${loaded.wabaId ? "Cargado" : "Sin cargar"}
              help="La cuenta de WhatsApp Business de las plantillas. No es un secreto." onChange=${(event) => setWaba(event.target.value)} />
          ${admin && html`<${Button} variant="secondary" disabled=${!waba} onClick=${saveWaba}>Guardar<//>`}
        </div>
        ${bot.verifyToken && html`
          <div class="pwa-divided">
            <h2 class="pwa-subtitle">Token de verificación</h2>
            <p class="pwa-muted">Meta lo pide una vez, al suscribir el webhook. Solo lo ve el cliente-admin.</p>
            <${CodeSnippet} title="verifyToken" variants=${[{ lang: "text", label: "Texto", code: bot.verifyToken }]} />
          </div>`}
      </div>
      <aside class="pwa-aside">
        <${CodeSnippet} title="URL del webhook" variants=${[{ lang: "text", label: "URL", code: `${WEBHOOK}/${botId}` }]} />
        <${CodeSnippet} title="Cargar o reemplazar un token" variants=${[{ lang: "bash", label: "cURL",
          code: curl("PUT", `chat/${botId}/credentials`, { appSecret: "<el App Secret>" }) }]} />
      </aside>
    </section>`;
}

// --- Cerebro e intents (RF24, RF02) ---------------------------------------------------------------------------------

function RuleEditor({ botId, rule, version, onClose, onSaved }) {
  const creating = !rule;
  const [text, setText] = useState(rule ? rule.rule : "");
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const route = creating ? `chat/${botId}/rules` : `chat/${botId}/rules/${rule.uuid}`;
  const body = creating ? { rules: [text] } : { rule: text };

  async function send(method) {
    setSaving(true);
    setError(null);
    const answer = await api(method, route, method === "DELETE" ? undefined : body);
    setSaving(false);
    if (answer.status === 200) onSaved();
    else if (answer.status === 422) {
      const errors = fieldErrors(answer);
      setError(/TOO_MANY_RULES/.test(answer.message || "") ? "Un bot tiene 50 reglas como mucho."
        : Object.values(errors)[0] || explain(answer));
    } else setError(answer.status === 409 ? "La regla ya dice eso." : explain(answer));
  }

  return html`
    <${SlideOver} title=${creating ? "Añadir regla" : "Editar regla"}
        subtitle=${creating ? `POST /chat/${botId}/rules` : `PUT /chat/${botId}/rules/${rule.uuid}`} onClose=${onClose}
        footer=${html`
          <${Button} variant="ghost" onClick=${onClose}>Cancelar<//>
          <${Button} variant="primary" icon="check" disabled=${!text.trim() || text.length > LIMITS.rule || saving}
              onClick=${() => send(creating ? "POST" : "PUT")}>Guardar regla · v${(version || 0) + 1}<//>`}>
      <${Field} id="rule-text" label="Regla" multiline rows=${5} required value=${text}
          onChange=${(event) => setText(event.target.value)}
          error=${text.length > LIMITS.rule ? `Como mucho ${LIMITS.rule} caracteres.` : error}
          help="Una instrucción para el bot, en lenguaje natural. Sin marcas de rol de un modelo." />
      ${!creating && html`<div class="pwa-divided">
        ${confirming ? html`
          <p class="pwa-notice">El motor deja de usarla. Se recupera volviendo a la versión anterior.</p>
          <span class="pwa-row-actions">
            <${Button} variant="ghost" size="sm" onClick=${() => setConfirming(false)}>No borrar<//>
            <${Button} variant="danger" size="sm" icon="trash" disabled=${saving} onClick=${() => send("DELETE")}>Borrar la regla<//>
          </span>` : html`<${Button} variant="danger-outline" size="sm" icon="trash" onClick=${() => setConfirming(true)}>Borrar regla<//>`}
      </div>`}
      <${CodeSnippet} title=${creating ? "Añadir la regla" : "Editar la regla"}
          variants=${[{ lang: "bash", label: "cURL", code: curl(creating ? "POST" : "PUT", route, body) }]} />
    <//>`;
}

function blankAction() {
  return { name: "", description: "", type: "api_call", parameters: [],
    apiCall: { method: "GET", url: "", headers: {}, query: {}, body: {}, authType: "none", authRef: "", timeout: 30 } };
}

// What PUT and POST of an intent take: the whole intent, the parameters of each action as they were.
function intentBody(draft) {
  return { name: draft.name, description: draft.description,
    samplePhrases: draft.phrases.split("\n").map((phrase) => phrase.trim()).filter(Boolean),
    actions: draft.actions.map((action) => ({ name: action.name, description: action.description, type: action.type || "api_call",
      parameters: (action.parameters || []).map((p) => ({ name: p.name, type: p.type || "string",
        description: p.description || "", required: Boolean(p.required), enum: p.enum || [] })),
      apiCall: { method: action.apiCall.method || "GET", url: action.apiCall.url || "", headers: action.apiCall.headers || {},
        query: action.apiCall.query || {}, body: action.apiCall.body || {}, authType: action.apiCall.authType || "none",
        authRef: action.apiCall.authRef || "", timeout: action.apiCall.timeout || 30 } })) };
}

function IntentEditor({ botId, intent, version, onClose, onSaved }) {
  const creating = !intent;
  const [draft, setDraft] = useState(() => ({
    name: intent ? intent.name : "", description: intent ? intent.description : "",
    phrases: intent ? (intent.samplePhrases || []).join("\n") : "",
    actions: intent ? intent.actions.filter((action) => action.apiCall).map((action) => ({ ...action })) : [blankAction()],
  }));
  const [errors, setErrors] = useState({});
  const [error, setError] = useState(null);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const route = creating ? `chat/${botId}/intents` : `chat/${botId}/intents/${intent.uuid}`;
  const body = intentBody(draft);

  const setAction = (index, change) => setDraft({ ...draft,
    actions: draft.actions.map((action, at) => (at === index ? change(action) : action)) });

  async function send(method) {
    setSaving(true);
    setError(null);
    setErrors({});
    const answer = await api(method, route, method === "DELETE" ? undefined : body);
    setSaving(false);
    if (answer.status === 200) onSaved();
    else if (answer.status === 422) { const found = fieldErrors(answer); setErrors(found); setError(Object.values(found)[0]); }
    else setError(explain(answer));
  }

  return html`
    <${SlideOver} title=${creating ? "Añadir intent" : `Editar intent ${intent.name}`}
        subtitle=${creating ? `POST /chat/${botId}/intents` : `PUT /chat/${botId}/intents/${intent.uuid}`} onClose=${onClose}
        footer=${html`
          <${Button} variant="ghost" onClick=${onClose}>Cancelar<//>
          <${Button} variant="primary" icon="check" disabled=${!draft.name.trim() || !draft.description.trim() || saving}
              onClick=${() => send(creating ? "POST" : "PUT")}>Guardar intent · v${(version || 0) + 1}<//>`}>
      <${Field} id="intent-name" label="Intent" mono required value=${draft.name}
          onChange=${(event) => setDraft({ ...draft, name: event.target.value })} help="Una palabra: lo que quiere la persona." />
      <${Field} id="intent-description" label="Descripción" required value=${draft.description}
          onChange=${(event) => setDraft({ ...draft, description: event.target.value })} />
      <${Field} id="intent-phrases" label="Frases de ejemplo" multiline rows=${3} value=${draft.phrases}
          onChange=${(event) => setDraft({ ...draft, phrases: event.target.value })} help="Una por línea." />
      <p class="pwa-notice" id="destinos">
        <strong>Destinos permitidos:</strong> solo <span class="mono">https</span> a un nombre público, sin usuario ni
        contraseña y sin cabecera <span class="mono">Host</span>. Nunca la red de la plataforma: ni <span class="mono">localhost</span>,
        ni direcciones privadas, ni nombres sin punto (RS16). Hive lo comprueba al guardar, al volver a una versión y al llamar.
      </p>
      ${draft.actions.map((action, index) => html`
        <fieldset key=${index} class="pwa-buttons">
          <legend>Acción ${index + 1}</legend>
          <${Field} id=${`action-${index}-name`} label="Nombre" mono value=${action.name}
              onChange=${(event) => setAction(index, (a) => ({ ...a, name: event.target.value }))} />
          <${Field} id=${`action-${index}-description`} label="Qué hace" value=${action.description}
              onChange=${(event) => setAction(index, (a) => ({ ...a, description: event.target.value }))} />
          <div class="pwa-pair">
            <div>
              <label class="pwa-label" for=${`action-${index}-method`}>Método</label>
              <select id=${`action-${index}-method`} class="pwa-select" value=${action.apiCall.method}
                  onChange=${(event) => setAction(index, (a) => ({ ...a, apiCall: { ...a.apiCall, method: event.target.value } }))}>
                ${["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => html`<option key=${method}>${method}</option>`)}
              </select>
            </div>
            <${Field} id=${`action-${index}-auth`} label="authRef" mono value=${action.apiCall.authRef || ""}
                help="El nombre de una credencial, nunca su valor."
                onChange=${(event) => setAction(index, (a) => ({ ...a, apiCall: { ...a.apiCall, authRef: event.target.value } }))} />
          </div>
          <${Field} id=${`action-${index}-url`} label="Destino" mono required value=${action.apiCall.url}
              error=${errors[`actions.${index}.apiCall.url`]}
              help="Los valores van entre llaves en la ruta o en la consulta: /pedidos/{id}."
              onChange=${(event) => setAction(index, (a) => ({ ...a, apiCall: { ...a.apiCall, url: event.target.value } }))} />
          <${Button} variant="ghost" size="sm" icon="x"
              onClick=${() => setDraft({ ...draft, actions: draft.actions.filter((_, at) => at !== index) })}>Quitar la acción<//>
        </fieldset>`)}
      <${Button} variant="secondary" size="sm" icon="plus"
          onClick=${() => setDraft({ ...draft, actions: [...draft.actions, blankAction()] })}>Añadir una acción<//>
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      ${!creating && html`<div class="pwa-divided">
        ${confirming ? html`
          <p class="pwa-notice">El motor deja de ofrecerlo. Se recupera volviendo a la versión anterior.</p>
          <span class="pwa-row-actions">
            <${Button} variant="ghost" size="sm" onClick=${() => setConfirming(false)}>No borrar<//>
            <${Button} variant="danger" size="sm" icon="trash" disabled=${saving} onClick=${() => send("DELETE")}>Borrar el intent<//>
          </span>` : html`<${Button} variant="danger-outline" size="sm" icon="trash" onClick=${() => setConfirming(true)}>Borrar intent<//>`}
      </div>`}
      <${CodeSnippet} title=${creating ? "Crear el intent" : "Editar el intent"}
          variants=${[{ lang: "bash", label: "cURL", code: curl(creating ? "POST" : "PUT", route, body) }]} />
    <//>`;
}

function destinationsOf(intent) {
  const calls = (intent.actions || []).filter((action) => action.apiCall);
  if (!calls.length) return "— (solo texto)";
  return calls.map((action) => {
    const url = action.apiCall.url || "";
    return `${action.apiCall.method} ${url.replace(/^https?:\/\//i, "").split("?")[0]}`;
  }).join(" · ");
}

function Brain({ botId, admin, config, intents, onSaved }) {
  const [editingRule, setEditingRule] = useState(undefined);
  const [editingIntent, setEditingIntent] = useState(undefined);
  const saved = () => { setEditingRule(undefined); setEditingIntent(undefined); onSaved(); };
  const rules = config.ruleList || [];

  const ruleColumns = useMemo(() => [
    { key: "n", label: "#", mono: true, width: "40px" },
    { key: "rule", label: "Regla" },
    ...(admin ? [{ key: "act", label: "", align: "right", width: "80px",
      render: (row) => html`<${Button} variant="ghost" size="sm" onClick=${() => setEditingRule(row)}>Editar<//>` }] : []),
  ], [admin]);
  const intentColumns = useMemo(() => [
    { key: "name", label: "Intent", mono: true, width: "140px" },
    { key: "description", label: "Qué quiere la persona" },
    { key: "dest", label: "Destino", mono: true },
    ...(admin ? [{ key: "act", label: "", align: "right", width: "80px",
      render: (row) => html`<${Button} variant="ghost" size="sm" onClick=${() => setEditingIntent(row)}>Editar<//>` }] : []),
  ], [admin]);

  return html`
    <section class="pwa-stack" aria-label="Cerebro e intents">
      <div class="pwa-card pwa-card-table">
        <div class="pwa-card-head">
          <div>
            <h2 class="pwa-subtitle">Reglas</h2>
            <span class="pwa-muted">${rules.length} de 50 · el motor las pone en el system del modelo, debajo de la identidad</span>
          </div>
          ${admin && rules.length < 50 && html`<${Button} variant="secondary" size="sm" icon="plus"
              onClick=${() => setEditingRule(null)}>Añadir regla<//>`}
        </div>
        ${rules.length === 0 ? html`<${EmptyState} title="Este bot todavía no tiene reglas" />`
          : html`<${DataTable} columns=${ruleColumns} rows=${rules.map((rule, at) => ({ ...rule, id: rule.uuid, n: String(at + 1) }))} />`}
      </div>
      <div class="pwa-card pwa-card-table">
        <div class="pwa-card-head">
          <div>
            <h2 class="pwa-subtitle">Intents</h2>
            <span class="pwa-muted">Lo que la persona puede pedir, y la API que llama cada uno. Los destinos se validan al
              guardar y al llamar (RS16).</span>
          </div>
          ${admin && html`<${Button} variant="secondary" size="sm" icon="plus" onClick=${() => setEditingIntent(null)}>Añadir intent<//>`}
        </div>
        ${intents.length === 0 ? html`<${EmptyState} title="Este bot todavía no tiene intents" />`
          : html`<${DataTable} columns=${intentColumns} rows=${intents.map((intent) => ({ ...intent, id: intent.uuid, dest: destinationsOf(intent) }))} />`}
      </div>
      <${PageCall} title="Leer la identidad y las reglas" method="GET" route=${`chat/${botId}/rules`} />
      ${editingRule !== undefined && html`<${RuleEditor} botId=${botId} rule=${editingRule} version=${config.version}
          onClose=${() => setEditingRule(undefined)} onSaved=${saved} />`}
      ${editingIntent !== undefined && html`<${IntentEditor} botId=${botId} intent=${editingIntent} version=${config.version}
          onClose=${() => setEditingIntent(undefined)} onSaved=${saved} />`}
    </section>`;
}

// --- Versiones (RF26) -----------------------------------------------------------------------------------------------

function Diff({ changes }) {
  const persona = Object.entries(changes.persona || {});
  const rules = changes.rules || { added: [], removed: [], changed: [] };
  const intents = changes.intents || { added: [], removed: [], changed: [] };
  const nothing = !persona.length && !rules.added.length && !rules.removed.length && !rules.changed.length
    && !intents.added.length && !intents.removed.length && !intents.changed.length;
  if (nothing) return html`<p class="pwa-muted">Sin cambios.</p>`;
  const label = Object.fromEntries(PERSONA_FIELDS);
  return html`
    <div class="pwa-diff">
      ${persona.map(([field, change]) => html`<div key=${field} class="pwa-diff-item">
        <span class="pwa-eyebrow">Identidad · ${label[field] || field}</span>
        ${change.from !== null && change.from !== undefined && html`<span class="pwa-del">${change.from}</span>`}
        <span class="pwa-add">${change.to}</span></div>`)}
      ${rules.changed.map((rule) => html`<div key=${rule.uuid} class="pwa-diff-item">
        <span class="pwa-eyebrow">Regla editada</span><span class="pwa-del">${rule.from}</span><span class="pwa-add">${rule.to}</span></div>`)}
      ${rules.added.map((rule) => html`<div key=${rule.uuid} class="pwa-diff-item">
        <span class="pwa-eyebrow">Regla añadida</span><span class="pwa-add">${rule.rule}</span></div>`)}
      ${rules.removed.map((rule) => html`<div key=${rule.uuid} class="pwa-diff-item">
        <span class="pwa-eyebrow">Regla quitada</span><span class="pwa-del">${rule.rule}</span></div>`)}
      ${intents.added.map((intent) => html`<div key=${intent.uuid} class="pwa-diff-item">
        <span class="pwa-eyebrow">Intent añadido</span><span class="pwa-add">${intent.name}</span></div>`)}
      ${intents.removed.map((intent) => html`<div key=${intent.uuid} class="pwa-diff-item">
        <span class="pwa-eyebrow">Intent quitado</span><span class="pwa-del">${intent.name}</span></div>`)}
      ${intents.changed.map((intent) => html`<div key=${intent.uuid} class="pwa-diff-item">
        <span class="pwa-eyebrow">Intent cambiado · ${intent.name}</span>
        <span class="pwa-muted">${intent.fields.join(", ")}</span></div>`)}
    </div>`;
}

function refusal(answer) {
  if (answer.status === 409) return "El bot ya tiene esta configuración.";
  if (answer.status !== 422 || !answer.data) return explain(answer);
  const parts = [];
  if (answer.data.persona) parts.push("la identidad");
  if ((answer.data.rules || []).length) parts.push(`${answer.data.rules.length} regla(s)`);
  if ((answer.data.intents || []).length) parts.push(`los intents ${answer.data.intents.join(", ")}`);
  return `No se puede volver a esta versión. Esto de ella ya no cumple las reglas de hoy (RS11, RS16): ${parts.join(", ") || "una parte"}.`;
}

function Versions({ botId, admin, current, onRestored }) {
  const [list, setList] = useState(null);
  const [picked, setPicked] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    api("GET", `chat/${botId}/versions`).then((answer) => {
      if (answer.status === 200) setList(answer.data.versions);
      else { setError(explain(answer)); setList([]); }
    });
  }, [botId, current]);

  useEffect(() => {
    if (picked === null) { setDetail(null); return; }
    setError(null);
    const against = picked === current ? undefined : current;
    api("GET", `chat/${botId}/versions/${picked}${against ? `?against=${against}` : ""}`).then((answer) => {
      setDetail(answer.status === 200 ? answer.data : null);
      if (answer.status !== 200) setError(explain(answer));
    });
  }, [botId, picked, current]);

  async function restore() {
    setRestoring(true);
    setError(null);
    const answer = await api("POST", `chat/${botId}/versions/${picked}/restore`);
    setRestoring(false);
    if (answer.status === 200) { setPicked(null); onRestored(); }
    else setError(refusal(answer));
  }

  const columns = useMemo(() => [
    { key: "version", label: "Versión", mono: true, width: "80px",
      render: (row) => (row.version === current ? html`<${Badge} tone="amber" dot=${false}>v${row.version}<//>` : `v${row.version}`) },
    { key: "createdAt", label: "Fecha (UTC)", mono: true, width: "150px", render: (row) => when(row.createdAt) },
    { key: "author", label: "Autor", render: (row) => who(row.author) },
    { key: "change", label: "Cambio", render: (row) => (row.restoredFrom ? `Vuelta a la v${row.restoredFrom}` : CHANGES[row.change] || row.change) },
  ], [current]);

  if (list === null) return html`<${LoadingState} kind="rows" message="Leyendo las versiones del bot…" />`;
  // hive-api compares the version with `against`, the current one: what going back to it would change.
  const changes = detail && detail.changes;
  return html`
    <section class="pwa-two pwa-two-wide" aria-label="Versiones">
      <div class="pwa-card pwa-card-table">
        <div class="pwa-card-head">
          <div>
            <h2 class="pwa-subtitle">Versiones de la configuración</h2>
            <span class="pwa-muted">Identidad, reglas e intents. Las credenciales de Meta y el verifyToken no se versionan.</span>
          </div>
          <${Badge} tone="muted" dot=${false}>${list.length} ${list.length === 1 ? "versión" : "versiones"}<//>
        </div>
        ${list.length === 0 ? html`<${EmptyState} title="Este bot todavía no tiene versiones">Se escriben con el primer cambio.<//>`
          : html`<${DataTable} columns=${columns} rows=${list.map((row) => ({ ...row, id: String(row.version) }))}
              selectedId=${picked === null ? undefined : String(picked)} onRowClick=${(row) => setPicked(row.version)} />`}
      </div>
      <aside class="pwa-aside">
        ${picked === null ? html`<div class="pwa-card"><span class="pwa-muted">Elige una versión para ver en qué se
            diferencia de la actual${admin ? " y volver a ella" : ""}.</span></div>` : html`
          <div class="pwa-card">
            <h2 class="pwa-subtitle">${picked === current ? `v${picked}, la actual` : `v${picked} frente a v${current} (actual)`}</h2>
            <span class="pwa-muted">${picked === current ? "Lo que cambió respecto de la anterior" : `Lo que cambiaría al volver a v${picked}`}</span>
            ${changes ? html`<${Diff} changes=${changes} />` : html`<${LoadingState} kind="spinner" message="Comparando…" />`}
            ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
            ${admin && picked !== current && html`
              <p class="pwa-notice">Volver crea la <span class="mono">v${current + 1}</span>, no reescribe nada. Hive revisa
                otra vez la identidad, las reglas y los destinos de los intents con las reglas de hoy.</p>
              <div class="pwa-form-actions">
                <${Button} variant="ghost" onClick=${() => setPicked(null)}>Cerrar<//>
                <span class="pwa-grow"></span>
                <${Button} variant="primary" disabled=${restoring} onClick=${restore}>Volver a v${picked}<//>
              </div>`}
          </div>`}
        ${error && picked === null && html`<p class="pwa-error" role="alert">${error}</p>`}
        <div class="pwa-card">
          <span class="pwa-eyebrow">Retención</span>
          <span class="pwa-muted">Las versiones viven mientras exista el bot y se borran con él. El cliente-analista las ve
            y compara; no vuelve atrás.</span>
        </div>
      </aside>
    </section>`;
}

// --- The card -------------------------------------------------------------------------------------------------------

export function BotCardPage({ where, admin, person, client }) {
  const [bot, setBot] = useState(null);
  const [config, setConfig] = useState(null);
  const [intents, setIntents] = useState([]);
  const [error, setError] = useState(null);

  async function load() {
    const [info, rules, found] = await Promise.all([api("GET", `chat/${where.bot}/info`), api("GET", `chat/${where.bot}/rules`),
      api("GET", `chat/${where.bot}/intents`)]);
    if (info.status !== 200) { setError(explain(info)); setBot({}); return; }
    setBot(info.data);
    setConfig(rules.status === 200 ? rules.data : null);
    setIntents(found.status === 200 && Array.isArray(found.data) ? found.data : []);
  }

  useEffect(() => { setBot(null); load(); }, [where.bot]);

  if (bot === null) return html`<${LoadingState} kind="spinner" message="Leyendo el bot…" />`;
  if (error) return html`<section aria-label="Bot"><p class="pwa-error" role="alert">${error}</p><p><a href="#/bots">Volver a los bots</a></p></section>`;
  const version = config ? config.version : 0;
  return html`
    <section aria-label="Bot" class="pwa-bot">
      <${BotHeader} client=${client} bot=${bot} version=${version} />
      <${Tabs} items=${TABS} value=${where.tab} onChange=${(tab) => go(`/bots/${where.bot}/${tab}`)} />
      ${where.tab === "identidad" && config && html`<${Identity} botId=${where.bot} admin=${admin} config=${config} onSaved=${load} />`}
      ${where.tab === "credenciales" && html`<${Credentials} botId=${where.bot} admin=${admin} bot=${bot} onSaved=${load} />`}
      ${where.tab === "cerebro" && config && html`<${Brain} botId=${where.bot} admin=${admin} config=${config} intents=${intents} onSaved=${load} />`}
      ${where.tab === "versiones" && html`<${Versions} botId=${where.bot} admin=${admin} current=${version} onRestored=${load} />`}
      ${where.tab === "plantillas" && html`<${Templates} botId=${where.bot} admin=${admin} />`}
      ${where.tab === "contactos" && (where.contact
        ? html`<${Contact} botId=${where.bot} contactId=${where.contact} admin=${admin} person=${person}
            onBack=${`#/bots/${where.bot}/contactos`} />`
        : html`<${Contacts} botId=${where.bot} onOpen=${(contact) => go(`/bots/${where.bot}/contactos/${contact}`)} />`)}
    </section>`;
}
