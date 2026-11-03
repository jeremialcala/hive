// The audit of the client (HIG-152, RF18): what its people and its credentials did through hive-api, the reads of
// personal data and what the response engine read, from GET /audit (HIG-95). Newest first, by pages of 50, with its
// filters, and all that matches as CSV. It is kept 12 months, and holds identifiers only: never a text or a number.
//
// Only for the cliente-admin: a cliente-analista has no entry in the side bar, because hive-api answers them 403.
import { api, explain, signIn } from "./api.js";
import { PageCall, PageHeader } from "./shell.js";

const { useState, useEffect, useMemo } = window.React;
const html = window.htm.bind(window.React.createElement);
const { Badge, Button, DataTable, EmptyState, Field, LoadingState, Tabs } = window.Hive;

const PAGE = 50;
// The categories of AuditEntry (CATEGORIES of hive-api), as the screen groups them.
const CATEGORIES = [
  { value: "", label: "Todo" },
  { value: "config", label: "Configuración" },
  { value: "people", label: "Personas" },
  { value: "keys", label: "Claves" },
  { value: "sends", label: "Envíos" },
  { value: "data", label: "Datos y borrados" },
  { value: "engine", label: "Motor" },
];
const RESULTS = {
  ok: { label: "OK", tone: "success" },
  denied: { label: "Denegado", tone: "warn" },
  failed: { label: "Fallido", tone: "error" },
};
const KINDS = { person: "Persona", machine: "Credencial de máquina", engine: "Motor", operator: "Operador" };
// The parameters of the routes, by what they name. One not here is shown by its own name.
const NAMES = { bot_id: "bot", contact_id: "contacto", client_id: "cliente", person_id: "persona", kid: "clave",
  template_id: "plantilla", guardrail_id: "tema", intent_id: "intent", rule_id: "regla", request_id: "envío",
  version: "versión" };
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const EMPTY = { since: "", until: "", actor: "", action: "", resource: "", result: "" };

function shortId(id) {
  return id && id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id || "";
}

// The moment of an entry, in UTC, to the second: the audit is read against logs and other clocks.
function utc(moment) {
  return moment ? `${new Date(moment).toISOString().slice(0, 19).replace("T", " ")}` : "";
}

// The day after a date of the form, at midnight UTC: `until` leaves out its own moment, and the person means the
// whole of the last day.
function dayAfter(date) {
  const next = new Date(`${date}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString().replace(".000Z", "Z");
}

// The query of GET /audit for a category and the filters of the form. Only what was filled goes.
export function auditQuery(category, filters, offset = 0) {
  const query = new URLSearchParams();
  if (filters.since) query.set("since", `${filters.since}T00:00:00Z`);
  if (filters.until) query.set("until", dayAfter(filters.until));
  if (category) query.set("category", category);
  for (const name of ["actor", "action", "resource", "result"]) {
    if (filters[name].trim()) query.set(name, filters[name].trim());
  }
  if (offset) query.set("offset", String(offset));
  query.set("limit", String(PAGE));
  return query.toString();
}

function Actor({ entry, emails }) {
  const [kind, id] = entry.actor.split(" ");
  const who = kind === "person" && emails[id] ? emails[id] : shortId(id);
  const what = entry.actorKind === "engine" && entry.worker ? `Motor · ${entry.worker}` : KINDS[entry.actorKind] || entry.actorKind;
  return html`<span class="pwa-audit-actor">
    <span>${what}</span><span class="mono pwa-muted" title=${entry.actor}>${who}</span></span>`;
}

function Resource({ resource }) {
  const names = Object.keys(resource || {}).sort();
  if (!names.length) return html`<span class="pwa-muted">—</span>`;
  return html`<span class="pwa-audit-ids">${names.map((name) => html`
    <span key=${name} class="mono" title=${`${name}=${resource[name]}`}>${NAMES[name] || name} ${shortId(resource[name])}</span>`)}</span>`;
}

function Result({ entry }) {
  const found = RESULTS[entry.result] || { label: entry.result, tone: "muted" };
  return html`<span title=${`HTTP ${entry.status}`}><${Badge} tone=${found.tone}>${found.label}<//></span>`;
}

function Filters({ draft, setDraft, error, onApply, onClear }) {
  const set = (name) => (event) => setDraft({ ...draft, [name]: event.target.value });
  return html`
    <form class="pwa-card pwa-audit-filters" aria-label="Filtros" onSubmit=${(event) => { event.preventDefault(); onApply(); }}>
      <div class="pwa-audit-grid">
        <${Field} id="audit-since" label="Desde (UTC)" type="date" value=${draft.since} onChange=${set("since")} />
        <${Field} id="audit-until" label="Hasta (UTC, incluido)" type="date" value=${draft.until} onChange=${set("until")} />
        <div>
          <label class="pwa-label" for="audit-result">Resultado</label>
          <select id="audit-result" class="pwa-select" value=${draft.result} onChange=${set("result")}>
            <option value="">Todos</option>
            ${Object.entries(RESULTS).map(([value, each]) => html`<option key=${value} value=${value}>${each.label}</option>`)}
          </select>
        </div>
        <${Field} id="audit-actor" label="Actor" mono placeholder="person <id> o key <kid>" value=${draft.actor}
          onChange=${set("actor")} />
        <${Field} id="audit-action" label="Acción" mono placeholder="rotate_verify_token" value=${draft.action}
          onChange=${set("action")} />
        <${Field} id="audit-resource" label="Recurso" mono placeholder="el id de un bot, un contacto…" value=${draft.resource}
          error=${error} onChange=${set("resource")} />
      </div>
      <div class="pwa-form-actions">
        <${Button} variant="ghost" type="button" onClick=${onClear}>Quitar los filtros<//>
        <span class="pwa-grow"></span>
        <${Button} variant="secondary" type="submit">Filtrar<//>
      </div>
    </form>`;
}

export function Audit({ client }) {
  const [category, setCategory] = useState("");
  const [draft, setDraft] = useState(EMPTY);
  const [filters, setFilters] = useState(EMPTY);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState(null);
  const [error, setError] = useState(null);
  const [fieldError, setFieldError] = useState(null);
  const [emails, setEmails] = useState({});
  const [csv, setCsv] = useState(null);

  useEffect(() => {
    // Who each person is, by their email: the audit keeps only their id.
    api("GET", "people").then((answer) => {
      if (answer.status === 200) setEmails(Object.fromEntries(answer.data.people.map((each) => [each.uuid, each.email])));
    });
  }, []);

  useEffect(() => {
    setPage(null);
    setError(null);
    api("GET", `audit?${auditQuery(category, filters, offset)}`).then((answer) => {
      if (answer.status === 200) setPage(answer.data);
      else { setError(explain(answer)); setPage({ total: 0, entries: [] }); }
    });
  }, [category, filters, offset]);

  function apply() {
    if (draft.resource.trim() && !UUID.test(draft.resource.trim())) {
      setFieldError("Es el id de un recurso: un UUID.");
      return;
    }
    if (draft.since && draft.until && draft.until < draft.since) {
      setFieldError(null);
      setError("«Hasta» va después de «Desde».");
      return;
    }
    setFieldError(null);
    setOffset(0);
    setFilters(draft);
  }

  function clear() {
    setFieldError(null);
    setDraft(EMPTY);
    setOffset(0);
    setFilters(EMPTY);
  }

  // All that matches, as hive-api writes it: by the portal, as a file, and said when it was cut at 50,000.
  async function download() {
    setCsv({ busy: true });
    const answer = await fetch(`/api/audit?${auditQuery(category, filters)}&format=csv`,
      { credentials: "same-origin", headers: { Accept: "text/csv" } });
    if (answer.status === 401) { signIn(); return; }
    if (answer.status !== 200) {
      const data = await answer.json().catch(() => ({}));
      setCsv({ error: explain({ status: answer.status, message: data && data.message }) });
      return;
    }
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await answer.blob());
    link.download = `auditoria-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    const total = Number(answer.headers.get("X-Audit-Total") || 0);
    setCsv(answer.headers.get("X-Audit-Truncated") === "true"
      ? { notice: `El CSV lleva las 50.000 entradas más recientes de ${total.toLocaleString("es")}. Acota las fechas para el resto.` }
      : { done: `Descargado: ${total.toLocaleString("es")} ${total === 1 ? "entrada" : "entradas"}.` });
  }

  const columns = useMemo(() => [
    { key: "at", label: "Fecha (UTC)", mono: true, width: "170px", render: (row) => utc(row.at) },
    { key: "actor", label: "Actor", render: (row) => html`<${Actor} entry=${row} emails=${emails} />` },
    { key: "action", label: "Acción", mono: true, render: (row) => html`<span title=${`${row.method} ${row.route}`}>${row.action}</span>` },
    { key: "resource", label: "Recurso", render: (row) => html`<${Resource} resource=${row.resource} />` },
    { key: "result", label: "Resultado", render: (row) => html`<${Result} entry=${row} />` },
  ], [emails]);

  const total = page ? page.total : 0;
  const shown = page ? page.entries.length : 0;
  const months = page && page.keptFor ? Math.round(page.keptFor / 30.4) : 12;
  return html`
    <section class="pwa-stack" aria-label="Auditoría">
      <${PageHeader} client=${client} title="Auditoría"
        subtitle=${`Se guarda ${months} meses. Solo identificadores: nunca un texto, un número ni un nombre.`}
        action=${html`<${Button} variant="primary" disabled=${csv && csv.busy} onClick=${download}>Exportar CSV<//>`} />
      ${csv && csv.error && html`<p class="pwa-error" role="alert">${csv.error}</p>`}
      ${csv && csv.notice && html`<p class="pwa-notice" role="status">${csv.notice}</p>`}
      ${csv && csv.done && html`<p class="pwa-success" role="status">${csv.done}</p>`}
      <${Filters} draft=${draft} setDraft=${setDraft} error=${fieldError} onApply=${apply} onClear=${clear} />
      <div class="pwa-card pwa-card-table">
        <div class="pwa-card-head">
          <${Tabs} compact items=${CATEGORIES} value=${category} onChange=${(value) => { setCategory(value); setOffset(0); }} />
          <span class="pwa-muted">${page ? `${total.toLocaleString("es")} ${total === 1 ? "entrada" : "entradas"}` : ""}</span>
        </div>
        ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
        ${page === null ? html`<${LoadingState} kind="rows" message="Leyendo la auditoría…" />`
          : shown === 0 ? html`<${EmptyState} title="No hay entradas aquí">Cada cambio, cada lectura de datos personales y
              lo que lee el motor quedan aquí en cuanto pasan.<//>`
          : html`<${DataTable} columns=${columns} rows=${page.entries.map((row, index) => ({ ...row, id: `${offset + index}` }))} />`}
        ${total > PAGE && html`
          <div class="pwa-audit-pages">
            <${Button} variant="ghost" size="sm" disabled=${offset === 0} onClick=${() => setOffset(Math.max(0, offset - PAGE))}>Más recientes<//>
            <span class="mono pwa-muted">${offset + 1}–${offset + shown} de ${total.toLocaleString("es")}</span>
            <${Button} variant="ghost" size="sm" disabled=${offset + shown >= total} onClick=${() => setOffset(offset + PAGE)}>Anteriores<//>
          </div>`}
      </div>
      <${PageCall} title="La auditoría, por la API" method="GET" route=${`audit?${auditQuery(category, filters, offset)}`} />
    </section>`;
}
