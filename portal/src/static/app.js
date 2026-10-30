// Hive's PWA, the client's area (HIG-87): the chatbots of the client, and in each, its templates and its contacts,
// inside the skeleton every screen shares (HIG-126, shell.js).
// No build: React 18, htm and the design system's components (window.Hive) come from the portal itself, so the CSP
// stays 'self'. The role comes from the session: a cliente-analista sees no button that would only answer 403.
import { session, signIn, api, explain } from "./api.js";
import { BotCardPage, TABS } from "./bot.js";
import { More, PageCall, PageHeader, Pending, Shell, applyTheme, go, isPending, savedTheme, sectionsFor } from "./shell.js";

const { useState, useEffect } = window.React;
const html = window.htm.bind(window.React.createElement);
const { BotCard, EmptyState, LoadingState } = window.Hive;

// #/ and #/bots · #/bots/<bot>/<tab> (bot.js: identidad by default) · #/bots/<bot>/contactos/<contact> · #/<section>
function route() {
  const [, kind, bot, tab, contact] = window.location.hash.replace(/^#/, "").split("/");
  if (kind === "bots" && bot) {
    return { section: "bots", bot, tab: TABS.some((each) => each.value === tab) ? tab : "identidad", contact };
  }
  return { section: kind || "bots" };
}

function Bots({ client }) {
  const [bots, setBots] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    api("GET", "bots").then((answer) => {
      if (answer.status === 200) setBots(answer.data.bots);
      else { setError(explain(answer)); setBots([]); }
    });
  }, []);
  return html`
    <section aria-label="Bots">
      <${PageHeader} client=${client} title="Bots"
          subtitle=${bots && bots.length ? `${bots.length} ${bots.length === 1 ? "bot" : "bots"}` : undefined} />
      ${bots === null ? html`<${LoadingState} kind="cards" message="Leyendo los bots del cliente…" />` : html`
        ${error && html`<p class="pwa-error" role="alert">${error}</p>`}
        ${bots.length === 0 ? html`<${EmptyState} title="Este cliente todavía no tiene bots" />` : html`
          <div class="pwa-cards">
            ${bots.map((bot) => html`<${BotCard} key=${bot.uuid} name=${bot.name}
                state=${["REG", "ACT", "ERR", "REV"].includes(bot.status) ? bot.status : "REG"} botId=${bot.uuid}
                channels=${bot.credentials && bot.credentials.whatsappToken ? ["whatsapp"] : []}
                onClick=${() => go(`/bots/${bot.uuid}/identidad`)} />`)}
          </div>`}`}
      <${PageCall} title="Listar los bots" method="GET" route="bots" />
    </section>`;
}

function Screen({ where, who, client, admin, theme, onTheme }) {
  if (where.section === "mas") return html`<${More} who=${who} client=${client} admin=${admin} theme=${theme} onTheme=${onTheme} />`;
  // A section that is not theirs, typed in the address: the same as one that does not exist.
  const theirs = sectionsFor(admin).some((section) => section.key === where.section);
  if (where.section === "bots" || !theirs) {
    return where.bot ? html`<${BotCardPage} where=${where} admin=${admin} person=${who.person} client=${client} />`
      : html`<${Bots} client=${client} />`;
  }
  return isPending(where.section) ? html`<${Pending} client=${client} section=${where.section} />` : html`<${Bots} client=${client} />`;
}

function App() {
  const [who, setWho] = useState(undefined);
  const [client, setClient] = useState(null);
  const [where, setWhere] = useState(route());
  const [theme, setTheme] = useState(savedTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    session().then((found) => {
      setWho(found);
      // Whose area it is, for the card of the client: without it, the card says so in general words.
      if (found) api("GET", "client").then((answer) => { if (answer.status === 200) setClient(answer.data); });
    });
    const moved = () => setWhere(route());
    window.addEventListener("hashchange", moved);
    return () => window.removeEventListener("hashchange", moved);
  }, []);

  if (who === undefined) return html`<${LoadingState} kind="spinner" message="Comprobando la sesión…" />`;
  if (who === null) {
    return html`<main class="pwa-main pwa-main-alone"><${EmptyState} title="Entra en el área del cliente de Hive" actionLabel="Entrar"
        onAction=${signIn}>Con tu cuenta y su segundo factor.<//></main>`;
  }
  const admin = who.role === "cliente-admin";
  const section = where.section === "mas" ? "mas" : (sectionsFor(admin).some((each) => each.key === where.section) ? where.section : "bots");
  return html`
    <${Shell} who=${who} client=${client} admin=${admin} section=${section} theme=${theme} onTheme=${setTheme}>
      <${Screen} where=${where} who=${who} client=${client} admin=${admin} theme=${theme} onTheme=${setTheme} />
    <//>`;
}

window.ReactDOM.createRoot(document.getElementById("app")).render(html`<${App} />`);
