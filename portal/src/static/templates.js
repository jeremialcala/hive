// The WhatsApp templates of a chatbot (HIG-87): the table with their state, the editor by components with its preview,
// and deleting one by typing its name. A cliente-analista sees them and creates, edits or deletes none.
import { api, curl, explain } from "./api.js";

const { useState, useEffect, useMemo } = window.React;
const html = window.htm.bind(window.React.createElement);
const { Badge, Button, CodeSnippet, ConfirmDestructive, DataTable, EmptyState, Field, LoadingState, SlideOver } =
  window.Hive;

// The state, with text and color: never color alone.
export const STATES = {
  APPROVED: ["success", "Aprobada"], PENDING: ["info", "En revisión"], REJECTED: ["error", "Rechazada"],
  PAUSED: ["warn", "En pausa"], DISABLED: ["muted", "Desactivada"], IN_APPEAL: ["amber", "En apelación"],
  PENDING_DELETION: ["muted", "Borrándose"], DELETED: ["muted", "Borrada"],
};
const EDITABLE = ["APPROVED", "REJECTED", "PAUSED"];
const CATEGORIES = { MARKETING: "Marketing", UTILITY: "Utilidad" };
const VALUE = /\{\{\s*(\d+)\s*\}\}/g;

export function stateBadge(status) {
  const [tone, label] = STATES[status] || ["neutral", status];
  return html`<${Badge} tone=${tone} dot>${label}<//>`;
}

// The numbers of the values of a text: {{1}}, {{2}}...
export function valuesIn(text) {
  return [...new Set([...(text || "").matchAll(VALUE)].map((found) => Number(found[1])))].sort((a, b) => a - b);
}

export function fill(text, values) {
  return (text || "").replace(VALUE, (whole, number) => values[Number(number) - 1] || whole);
}

function componentsOf(template) {
  const found = (kind) => (template.components || []).find((part) => part.type === kind) || {};
  return { header: found("HEADER").text || "", body: found("BODY").text || "", footer: found("FOOTER").text || "",
    examples: ((found("BODY").example || {}).body_text || [[]])[0] || [], buttons: found("BUTTONS").buttons || [] };
}

// What Meta is handed: a body with an example for each value, and maybe a header, a footer and buttons.
function componentsFor(draft) {
  const parts = [];
  if (draft.header.trim()) parts.push({ type: "HEADER", format: "TEXT", text: draft.header.trim() });
  const body = { type: "BODY", text: draft.body };
  if (valuesIn(draft.body).length) body.example = { body_text: [valuesIn(draft.body).map((n) => draft.examples[n - 1] || "")] };
  parts.push(body);
  if (draft.footer.trim()) parts.push({ type: "FOOTER", text: draft.footer.trim() });
  const buttons = draft.buttons.filter((button) => button.text.trim()).map((button) => (
    button.type === "URL" ? { type: "URL", text: button.text, url: button.url }
      : button.type === "PHONE_NUMBER" ? { type: "PHONE_NUMBER", text: button.text, phone_number: button.phone_number }
        : { type: "QUICK_REPLY", text: button.text }));
  if (buttons.length) parts.push({ type: "BUTTONS", buttons });
  return parts;
}

// The check Hive makes, said before it is sent: so the person sees what to fix.
function problemsOf(draft, creating) {
  const problems = {};
  if (creating && !/^[a-z0-9_]{1,512}$/.test(draft.name)) problems.name = "Solo minúsculas, números y _.";
  if (creating && !/^[a-z]{2,3}(_[A-Za-z0-9]{2,8})?$/.test(draft.language)) problems.language = "Como es, en o es_MX.";
  if (!draft.body.trim()) problems.body = "El cuerpo es obligatorio.";
  const numbers = valuesIn(draft.body);
  if (numbers.some((number, index) => number !== index + 1)) problems.body = "Los valores van {{1}}, {{2}}… sin huecos.";
  numbers.forEach((number) => {
    if (!(draft.examples[number - 1] || "").trim()) problems[`example${number}`] = "Cada valor lleva un ejemplo.";
  });
  if (valuesIn(draft.header).length) problems.header = "La cabecera no lleva valores.";
  if (valuesIn(draft.footer).length) problems.footer = "El pie no lleva valores.";
  draft.buttons.forEach((button, index) => {
    if (button.type === "URL" && !/^https:\/\/\S+$/.test(button.url || "")) problems[`button${index}`] = "Un enlace https://…";
    if (button.type === "PHONE_NUMBER" && !/^\+?[0-9]{6,20}$/.test(button.phone_number || "")) {
      problems[`button${index}`] = "Un número con su prefijo, como +584121234567.";
    }
  });
  return problems;
}

export function Preview({ header, body, footer, values, buttons }) {
  return html`
    <div class="pwa-preview" aria-label="Vista previa del mensaje">
      ${header && html`<p class="pwa-preview-header">${header}</p>`}
      <p class="pwa-preview-body">${fill(body, values) || "El cuerpo del mensaje"}</p>
      ${footer && html`<p class="pwa-preview-footer">${footer}</p>`}
      ${(buttons || []).filter((button) => button.text).map((button, index) => html`
        <span key=${index} class="pwa-preview-button">${button.text}</span>`)}
    </div>`;
}

function Editor({ botId, template, onClose, onSaved }) {
  const creating = !template;
  const start = template ? componentsOf(template) : { header: "", body: "", footer: "", examples: [], buttons: [] };
  const [draft, setDraft] = useState({ name: template ? template.name : "", language: template ? template.language : "es",
    category: template ? template.category : "UTILITY", ...start });
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  // A problem shows once its field was written in: an empty form is not a list of mistakes.
  const [touched, setTouched] = useState({});
  const set = (field) => (event) => {
    setTouched({ ...touched, [field]: true });
    setDraft({ ...draft, [field]: event.target.value });
  };
  const problems = problemsOf(draft, creating);
  const shown = (key) => (touched[key] ? problems[key] : undefined);
  const numbers = valuesIn(draft.body);
  const body = creating ? { name: draft.name, language: draft.language, category: draft.category,
    components: componentsFor(draft) } : { components: componentsFor(draft) };
  const route = creating ? `chat/${botId}/templates` : `chat/${botId}/templates/${template.id}`;

  async function save() {
    setSaving(true);
    setError(null);
    const answer = await api(creating ? "POST" : "PUT", route, body);
    setSaving(false);
    if (answer.status === 200 || answer.status === 201) onSaved();
    else setError(explain(answer));
  }

  const setButton = (index, field) => (event) => {
    const buttons = draft.buttons.map((button, at) => (at === index ? { ...button, [field]: event.target.value } : button));
    setTouched({ ...touched, [`button${index}`]: true });
    setDraft({ ...draft, buttons });
  };

  return html`
    <${SlideOver} title=${creating ? "Nueva plantilla" : `Editar ${template.name}`}
        subtitle=${creating ? `POST /chat/${botId}/templates` : `PUT /chat/${botId}/templates/${template.id}`}
        onClose=${onClose}
        footer=${html`
          <${Button} variant="secondary" onClick=${onClose}>Cancelar<//>
          <${Button} variant="primary" disabled=${saving || Object.keys(problems).length > 0} onClick=${save}>
            ${creating ? "Enviar a revisión" : "Guardar y volver a revisión"}<//>`}>
      <p class="pwa-notice">
        Meta revisa cada plantilla antes de dejarla usar: queda «En revisión» hasta que la apruebe.
        ${!creating && " Una plantilla aprobada no cambia de categoría, y Meta limita sus ediciones: una al día y diez al mes."}
      </p>
      ${creating && html`
        <${Field} id="tpl-name" label="Nombre" mono required value=${draft.name} onChange=${set("name")}
            help="Minúsculas, números y _: así la ve Meta." error=${shown("name")} />
        <${Field} id="tpl-language" label="Idioma" mono value=${draft.language} onChange=${set("language")}
            error=${shown("language")} />
        <label class="pwa-label" for="tpl-category">Categoría</label>
        <select id="tpl-category" class="pwa-select" value=${draft.category} onChange=${set("category")}>
          ${Object.entries(CATEGORIES).map(([value, label]) => html`<option key=${value} value=${value}>${label}</option>`)}
        </select>`}
      <${Field} id="tpl-header" label="Cabecera (opcional)" value=${draft.header} onChange=${set("header")}
          error=${shown("header")} />
      <${Field} id="tpl-body" label="Cuerpo" multiline rows=${5} required value=${draft.body} onChange=${set("body")}
          help="Los valores van como {{1}}, {{2}}…, desde 1 y sin huecos." error=${shown("body")} />
      ${numbers.map((number) => html`
        <${Field} key=${number} id=${`tpl-example-${number}`} label=${`Ejemplo de {{${number}}}`} required
            value=${draft.examples[number - 1] || ""} error=${shown(`example${number}`)}
            onChange=${(event) => { setTouched({ ...touched, [`example${number}`]: true });
              const examples = [...draft.examples]; examples[number - 1] = event.target.value;
              setDraft({ ...draft, examples }); }} />`)}
      <${Field} id="tpl-footer" label="Pie (opcional)" value=${draft.footer} onChange=${set("footer")}
          error=${shown("footer")} />
      <fieldset class="pwa-buttons">
        <legend>Botones (hasta 10)</legend>
        ${draft.buttons.map((button, index) => html`
          <div key=${index} class="pwa-button-row">
            <select class="pwa-select" aria-label="Tipo de botón" value=${button.type} onChange=${setButton(index, "type")}>
              <option value="QUICK_REPLY">Respuesta rápida</option>
              <option value="URL">Enlace</option>
              <option value="PHONE_NUMBER">Llamada</option>
            </select>
            <${Field} label="Texto" value=${button.text} onChange=${setButton(index, "text")} />
            ${button.type === "URL" && html`<${Field} label="Enlace" mono value=${button.url || ""}
                onChange=${setButton(index, "url")} error=${shown(`button${index}`)} />`}
            ${button.type === "PHONE_NUMBER" && html`<${Field} label="Número" mono value=${button.phone_number || ""}
                onChange=${setButton(index, "phone_number")} error=${shown(`button${index}`)} />`}
            <${Button} variant="ghost" size="sm" icon="x" ariaLabel="Quitar el botón"
                onClick=${() => setDraft({ ...draft, buttons: draft.buttons.filter((_, at) => at !== index) })} />
          </div>`)}
        ${draft.buttons.length < 10 && html`
          <${Button} variant="secondary" size="sm" icon="plus"
              onClick=${() => setDraft({ ...draft, buttons: [...draft.buttons, { type: "QUICK_REPLY", text: "" }] })}>
            Añadir un botón<//>`}
      </fieldset>
      <h3 class="pwa-subtitle">Vista previa</h3>
      <${Preview} header=${draft.header} body=${draft.body} footer=${draft.footer} values=${draft.examples}
          buttons=${draft.buttons} />
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      <${CodeSnippet} title=${creating ? "Crear la plantilla" : "Editar la plantilla"}
          variants=${[{ lang: "bash", label: "cURL", code: curl(creating ? "POST" : "PUT", route, body) }]} />
    <//>`;
}

export function Templates({ botId, admin }) {
  const [templates, setTemplates] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(undefined);
  const [deleting, setDeleting] = useState(null);

  async function load(sync = false) {
    setError(null);
    const answer = await api("GET", `chat/${botId}/templates${sync ? "?sync=true" : ""}`);
    if (answer.status === 200) setTemplates(answer.data.templates);
    else {
      setError(explain(answer));
      if (templates === null) setTemplates([]);
    }
  }

  async function remove(template) {
    const answer = await api("DELETE", `chat/${botId}/templates/${template.id}`);
    setDeleting(null);
    if (answer.status === 200) load();
    else setError(explain(answer));
  }

  useEffect(() => { load(); }, [botId]);

  const columns = useMemo(() => [
    { key: "name", label: "Nombre", mono: true },
    { key: "language", label: "Idioma", mono: true },
    { key: "category", label: "Categoría", render: (row) => CATEGORIES[row.category] || row.category },
    { key: "status", label: "Estado", render: (row) => html`<span>${stateBadge(row.status)}
        ${row.reason && html` <span class="pwa-muted">${row.reason}</span>`}</span>` },
    ...(admin ? [{ key: "actions", label: "", align: "right", render: (row) => html`<span class="pwa-row-actions">
        <${Button} variant="ghost" size="sm" disabled=${!EDITABLE.includes(row.status)}
            onClick=${() => setEditing(row)}>Editar<//>
        <${Button} variant="ghost" size="sm" icon="trash" disabled=${row.status === "DISABLED"}
            ariaLabel=${`Borrar ${row.name}`} onClick=${() => setDeleting(row)} />
      </span>` }] : []),
  ], [admin]);

  if (templates === null) return html`<${LoadingState} kind="rows" message="Leyendo las plantillas del bot…" />`;
  return html`
    <section aria-label="Plantillas">
      <div class="pwa-toolbar">
        <${Button} variant="secondary" onClick=${() => load(true)}>Sincronizar con Meta<//>
        ${admin && html`<${Button} variant="primary" icon="plus" onClick=${() => setEditing(null)}>Nueva plantilla<//>`}
      </div>
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      ${templates.length === 0 ? html`<${EmptyState} title="Este bot todavía no tiene plantillas"
          actionLabel=${admin ? "Crear la primera" : undefined} onAction=${admin ? () => setEditing(null) : undefined}>
          Sincroniza con Meta para traer las que ya existan en la cuenta de WhatsApp.<//>`
        : html`<${DataTable} columns=${columns} rows=${templates.map((row) => ({ ...row, id: row.id || row.name }))} />`}
      <${CodeSnippet} title="Listar las plantillas"
          variants=${[{ lang: "bash", label: "cURL", code: curl("GET", `chat/${botId}/templates?sync=true`) }]} />
      ${editing !== undefined && html`<${Editor} botId=${botId} template=${editing}
          onClose=${() => setEditing(undefined)} onSaved=${() => { setEditing(undefined); load(); }} />`}
      ${deleting && html`<${ConfirmDestructive} title=${`Borrar la plantilla ${deleting.name}`} expected=${deleting.name}
          fieldLabel="Escribe el nombre de la plantilla para confirmar"
          facts=${[{ label: "Idioma", value: deleting.language }, { label: "Meta", value: deleting.id || "" }]}
          confirmLabel="Borrar en Meta" onCancel=${() => setDeleting(null)} onConfirm=${() => remove(deleting)}>
          Se borra en Meta, solo en este idioma, y Meta no deja reutilizar el nombre durante 30 días.<//>`}
    </section>`;
}
