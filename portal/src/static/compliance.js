// The compliance of a chatbot (HIG-149, RF28): how many of its answers the evaluator approved, had rewritten or
// replaced, how the people of the client rated them, and the last answers to rate. Opening one shows the whole
// conversation, without the contact's number, to rate each answer of the bot: good or bad, and why if they want.
//
// Only people rate: hive-api answers 403 to a machine credential. The answers go by their opaque reference (`ref`),
// never by Meta's id, which in WhatsApp carries the contact's number.
import { api, curl, explain } from "./api.js";
import { asThread, when } from "./contacts.js";

const { useState, useEffect, useMemo } = window.React;
const html = window.htm.bind(window.React.createElement);
const { Badge, Button, CodeSnippet, DataTable, EmptyState, Field, LoadingState, MetricCard, Tabs, Thread } = window.Hive;

const PERIODS = [{ value: "7", label: "7 días" }, { value: "30", label: "30 días" }, { value: "all", label: "Todo" }];
const FILTERS = [{ value: "unrated", label: "Sin calificar" }, { value: "flagged", label: "Marcadas" },
  { value: "all", label: "Todas" }];
// What the evaluator decided (SentReply.evaluation, HIG-147), in words and in color.
const VERDICTS = {
  approved: { label: "Aprobada", tone: "success" },
  rewritten: { label: "Reescrita", tone: "warn" },
  replaced: { label: "Sustituida", tone: "error" },
  fixed: { label: "Texto fijo", tone: "muted" },
  unevaluated: { label: "Sin evaluar", tone: "error" },
};
const MAX_COMMENT = 500;

function since(period) {
  return period === "all" ? null : new Date(Date.now() - Number(period) * 86400000).toISOString();
}

function verdict(evaluation) {
  const found = VERDICTS[evaluation && evaluation.verdict];
  return found ? html`<${Badge} tone=${found.tone}>${found.label}<//>` : "";
}

function score(ratings) {
  if (!ratings.up && !ratings.down) return html`<span class="pwa-muted">Sin calificar</span>`;
  return `${ratings.up} ↑ · ${ratings.down} ↓`;
}

function Metrics({ metrics }) {
  const { answers, verdicts, ratings } = metrics;
  const approved = answers ? Math.round((verdicts.approved / answers) * 100) : 0;
  return html`
    <div class="pwa-metrics">
      <${MetricCard} label="Aprobadas" value=${answers ? approved : "—"} unit=${answers ? "%" : ""}
        hint=${`${verdicts.approved} de ${answers} respuestas evaluadas`} />
      <${MetricCard} label="Corregidas" value=${verdicts.rewritten + verdicts.replaced}
        hint=${`${verdicts.rewritten} reescritas · ${verdicts.replaced} sustituidas`} />
      <${MetricCard} label="Textos fijos" value=${metrics.fixedTexts}
        hint="Incluye lo que el clasificador bloqueó antes de responder" />
      <${MetricCard} label="Calificadas" value=${ratings.answers} unit=${`${ratings.up} ↑ · ${ratings.down} ↓`}
        hint="Respuestas que una persona del cliente calificó" />
    </div>`;
}

// What the person thinks of an answer: the score is chosen in the thread; the comment, if any, here.
function RatingForm({ pending, saving, error, onSave, onCancel }) {
  const [comment, setComment] = useState("");
  return html`
    <div class="pwa-card">
      <h2 class="pwa-subtitle">${pending.score === "up" ? "Respuesta correcta" : "Respuesta incorrecta"}</h2>
      <${Field} id="rating-comment" label="Comentario (opcional)" multiline rows=${3} value=${comment}
        help=${`${comment.length} / ${MAX_COMMENT}`} onChange=${(event) => setComment(event.target.value.slice(0, MAX_COMMENT))} />
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      <div class="pwa-form-actions">
        <${Button} variant="ghost" onClick=${onCancel}>Cancelar<//>
        <span class="pwa-grow"></span>
        <${Button} variant="primary" disabled=${saving} onClick=${() => onSave(comment)}>Guardar<//>
      </div>
    </div>`;
}

function Conversation({ botId, picked, person, onRated }) {
  const [messages, setMessages] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(null);
  const [saving, setSaving] = useState(false);
  const [round, setRound] = useState(0); // Cancelling a rating takes the thread back to what was saved.
  const contactId = picked.contact.uuid;

  async function load() {
    const answer = await api("GET", `chat/${botId}/contacts/${contactId}/conversation`);
    if (answer.status === 200) setMessages(answer.data.messages);
    else { setError(explain(answer)); setMessages([]); }
  }

  useEffect(() => { setMessages(null); setPending(null); setError(null); load(); }, [botId, contactId]);

  async function save(comment) {
    setSaving(true);
    setError(null);
    const answer = await api("PUT", `chat/${botId}/contacts/${contactId}/ratings`,
      { ref: pending.ref, score: pending.score, ...(comment.trim() ? { comment } : {}) });
    setSaving(false);
    if (answer.status !== 200) { setError(explain(answer)); return; }
    setPending(null);
    await load();
    onRated();
  }

  function cancel() { setPending(null); setError(null); setRound(round + 1); }

  const thread = useMemo(() => {
    if (!messages) return [];
    const shown = asThread(messages, person);
    return shown.map((each, index) => {
      const message = messages[index];
      if (message.direction !== "out" || message.origin === "client") return each;
      const mine = (message.ratings || []).find((rating) => rating.ratedBy === `person ${person}`);
      const found = VERDICTS[message.evaluation && message.evaluation.verdict];
      // The answer chosen in the table, and what the evaluator decided of each: said in its line, not as a guardrail.
      const parts = [each.id === picked.ref ? "Respuesta elegida" : "Bot", found && found.label].filter(Boolean);
      return { ...each, author: parts.join(" · "), rating: mine && mine.score };
    });
  }, [messages, person, picked.ref]);

  if (messages === null) return html`<${LoadingState} kind="spinner" message="Leyendo la conversación…" />`;
  return html`
    <div class="pwa-aside">
    <div class="pwa-card">
      <h2 class="pwa-subtitle">${picked.contact.firstName || "Sin nombre"}</h2>
      <span class="pwa-muted">Se abre la conversación completa, sin el número del contacto. Cada apertura queda en la
        auditoría del cliente.</span>
      ${thread.length ? html`<${Thread} key=${round} ariaLabel="Conversación" messages=${thread}
          onRate=${(message, chosen) => { setError(null); setPending({ ref: message.id, score: chosen }); }} />`
        : html`<${EmptyState} title="La conversación ya no está">Caducó con el historial.<//>`}
      ${error && !pending && html`<p class="pwa-error" role="alert">${error}</p>`}
    </div>
    ${pending && html`<${RatingForm} key=${`${pending.ref}:${pending.score}`} pending=${pending} saving=${saving}
        error=${error} onSave=${save} onCancel=${cancel} />`}
    </div>`;
}

export function Compliance({ botId, person }) {
  const [period, setPeriod] = useState("30");
  const [filter, setFilter] = useState("unrated");
  const [metrics, setMetrics] = useState(null);
  const [answers, setAnswers] = useState(null);
  const [picked, setPicked] = useState(null);
  const [error, setError] = useState(null);

  async function loadMetrics() {
    const from = since(period);
    const answer = await api("GET", `chat/${botId}/compliance${from ? `?since=${encodeURIComponent(from)}` : ""}`);
    if (answer.status === 200) setMetrics(answer.data);
    else setError(explain(answer));
  }

  async function loadAnswers() {
    const answer = await api("GET", `chat/${botId}/compliance/answers?filter=${filter}`);
    if (answer.status === 200) setAnswers(answer.data.answers);
    else { setError(explain(answer)); setAnswers([]); }
  }

  useEffect(() => { setMetrics(null); loadMetrics(); }, [botId, period]);
  useEffect(() => { setAnswers(null); loadAnswers(); }, [botId, filter]);

  const columns = useMemo(() => [
    { key: "contact", label: "Contacto", render: (row) => row.contact.firstName || html`<span class="pwa-muted">Sin nombre</span>` },
    { key: "sentAt", label: "Fecha", mono: true, width: "150px", render: (row) => when(row.sentAt) },
    { key: "evaluation", label: "Evaluador", render: (row) => verdict(row.evaluation) },
    { key: "ratings", label: "Calificación", render: (row) => score(row.ratings) },
  ], []);

  return html`
    <section class="pwa-stack" aria-label="Cumplimiento">
      <div class="pwa-card-head pwa-bare">
        <div>
          <h2 class="pwa-subtitle">Cumplimiento</h2>
          <span class="pwa-muted">Lo que decidió el evaluador de cada respuesta y lo que opinan las personas del cliente.</span>
        </div>
        <${Tabs} compact items=${PERIODS} value=${period} onChange=${setPeriod} />
      </div>
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      ${metrics ? html`<${Metrics} metrics=${metrics} />` : html`<${LoadingState} kind="rows" message="Contando…" />`}
      <div class="pwa-two pwa-two-wide">
        <div class="pwa-card pwa-card-table">
          <div class="pwa-card-head">
            <div>
              <h2 class="pwa-subtitle">Respuestas para calificar</h2>
              <span class="pwa-muted">Marcadas: el evaluador no las aprobó tal cual, o alguien las calificó mal.</span>
            </div>
            <${Tabs} compact items=${FILTERS} value=${filter} onChange=${(value) => { setFilter(value); setPicked(null); }} />
          </div>
          ${answers === null ? html`<${LoadingState} kind="rows" message="Leyendo las respuestas…" />`
            : answers.length === 0 ? html`<${EmptyState} title="No hay respuestas aquí">Las respuestas que el evaluador
                juzga aparecen en cuanto el bot contesta.<//>`
            : html`<${DataTable} columns=${columns} rows=${answers.map((row) => ({ ...row, id: row.ref }))}
                selectedId=${picked ? picked.ref : undefined} onRowClick=${(row) => setPicked(row)} />`}
        </div>
        <aside class="pwa-aside">
          ${picked ? html`<${Conversation} botId=${botId} picked=${picked} person=${person}
              onRated=${() => { loadAnswers(); loadMetrics(); }} />`
            : html`<div class="pwa-card"><span class="pwa-muted">Elige una respuesta para abrir su conversación y
                calificarla.</span></div>`}
          <div class="pwa-card">
            <span class="pwa-eyebrow">Desde una integración</span>
            <${CodeSnippet} title="Las métricas y una calificación" variants=${[{ lang: "bash", label: "cURL", code:
              `${curl("GET", `chat/${botId}/compliance?since=2026-10-01T00:00:00Z`)}\n\n${curl("PUT",
                `chat/${botId}/contacts/<contacto>/ratings`, { ref: "<ref de la conversación>", score: "down",
                  comment: "Demasiado larga" })}` }]} />
            <span class="pwa-muted">Solo califican las personas: una credencial de máquina recibe 403.</span>
          </div>
        </aside>
      </div>
    </section>`;
}
