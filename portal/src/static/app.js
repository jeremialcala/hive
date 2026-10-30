// Hive's PWA, the client's area (HIG-87): the chatbots of the client, and in each, its templates and its contacts.
// No build: React 18, htm and the design system's components (window.Hive) come from the portal itself, so the CSP
// stays 'self'. The role comes from the session: a cliente-analista sees no button that would only answer 403.
import { session, signIn, signOut, api, explain } from "./api.js";
import { Templates } from "./templates.js";
import { Contacts, Contact } from "./contacts.js";

const { useState, useEffect } = window.React;
const html = window.htm.bind(window.React.createElement);
const { Badge, BotCard, Button, EmptyState, LoadingState, Tabs } = window.Hive;

const ROLES = { "cliente-admin": "Administrador", "cliente-analista": "Analista" };

// #/ · #/bots/<bot>/plantillas · #/bots/<bot>/contactos · #/bots/<bot>/contactos/<contact>
function route() {
  const [, kind, bot, tab, contact] = window.location.hash.replace(/^#/, "").split("/");
  return kind === "bots" && bot ? { bot, tab: tab === "contactos" ? "contactos" : "plantillas", contact } : {};
}

function go(path) {
  window.location.hash = path;
}

function Bots() {
  const [bots, setBots] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    api("GET", "bots").then((answer) => {
      if (answer.status === 200) setBots(answer.data.bots);
      else { setError(explain(answer)); setBots([]); }
    });
  }, []);
  if (bots === null) return html`<${LoadingState} kind="cards" message="Leyendo los bots del cliente…" />`;
  return html`
    <section aria-label="Bots">
      <h2 class="heading-lg">Bots</h2>
      ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
      ${bots.length === 0 ? html`<${EmptyState} title="Este cliente todavía no tiene bots" />` : html`
        <div class="pwa-cards">
          ${bots.map((bot) => html`<${BotCard} key=${bot.uuid} name=${bot.name}
              state=${["REG", "ACT", "ERR", "REV"].includes(bot.status) ? bot.status : "REG"} botId=${bot.uuid}
              channels=${bot.credentials && bot.credentials.whatsappToken ? ["whatsapp"] : []}
              onClick=${() => go(`/bots/${bot.uuid}/plantillas`)} />`)}
        </div>`}
    </section>`;
}

function Bot({ where, admin, person }) {
  const tabs = [{ value: "plantillas", label: "Plantillas" }, { value: "contactos", label: "Contactos" }];
  return html`
    <section aria-label="Bot">
      <p><a href="#/">← Bots</a></p>
      <${Tabs} items=${tabs} value=${where.tab} onChange=${(tab) => go(`/bots/${where.bot}/${tab}`)} />
      ${where.tab === "plantillas" ? html`<${Templates} botId=${where.bot} admin=${admin} />`
        : where.contact ? html`<${Contact} botId=${where.bot} contactId=${where.contact} admin=${admin} person=${person}
            onBack=${`#/bots/${where.bot}/contactos`} />`
          : html`<${Contacts} botId=${where.bot} onOpen=${(contact) => go(`/bots/${where.bot}/contactos/${contact}`)} />`}
    </section>`;
}

function App() {
  const [who, setWho] = useState(undefined);
  const [where, setWhere] = useState(route());

  useEffect(() => {
    session().then(setWho);
    const moved = () => setWhere(route());
    window.addEventListener("hashchange", moved);
    return () => window.removeEventListener("hashchange", moved);
  }, []);

  if (who === undefined) return html`<${LoadingState} kind="spinner" message="Comprobando la sesión…" />`;
  if (who === null) {
    return html`<main class="pwa-main"><${EmptyState} title="Entra en el área del cliente de Hive" actionLabel="Entrar"
        onAction=${signIn}>Con tu cuenta y su segundo factor.<//></main>`;
  }
  const admin = who.role === "cliente-admin";
  return html`
    <div class="pwa-shell">
      <header class="pwa-topbar">
        <a class="pwa-brand" href="#/">Hive</a>
        <span class="pwa-topbar-end">
          <${Badge} tone=${admin ? "amber" : "info"}>${ROLES[who.role] || who.role}<//>
          <${Button} variant="ghost" size="sm" onClick=${signOut}>Cerrar sesión<//>
        </span>
      </header>
      <main class="pwa-main">${where.bot ? html`<${Bot} where=${where} admin=${admin} person=${who.person} />` : html`<${Bots} />`}</main>
    </div>`;
}

window.ReactDOM.createRoot(document.getElementById("app")).render(html`<${App} />`);
