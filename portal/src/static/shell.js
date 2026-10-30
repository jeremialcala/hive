// The skeleton every screen of the client's area shares (HIG-126, the canvas «Pantallas de Hive»): the side bar of
// 240 px with its eight sections and the card of the client, the TabBar that takes its place on a phone, the header
// of a page with one primary action, and the cURL of its call at its foot. The theme is dark unless the person picks
// the light one, and the PWA remembers it in this browser only.
//
// What a cliente-analista would get a 403 for is not shown to them: hive-api decides, this only spares the click.
import { curl, signOut } from "./api.js";

const html = window.htm.bind(window.React.createElement);
const { Badge, Button, Cell, CodeSnippet, EmptyState, Icon, TabBar } = window.Hive;

export const ROLES = { "cliente-admin": "cliente-admin", "cliente-analista": "cliente-analista" };

// The sections of the side bar, in its order. `admin`: only for whoever may use it; the routes under them answer 403
// to a cliente-analista (ANALYST_ROUTES of hive-api).
export const SECTIONS = [
  { key: "inicio", label: "Visión general", icon: "home" },
  { key: "bots", label: "Bots", icon: "bot" },
  { key: "contactos", label: "Contactos", icon: "message-circle" },
  { key: "plantillas", label: "Plantillas", icon: "copy" },
  { key: "personas", label: "Personas", icon: "users", admin: true },
  { key: "credenciales", label: "Credenciales", icon: "key", admin: true },
  { key: "integraciones", label: "Integraciones", icon: "sliders" },
  { key: "auditoria", label: "Auditoría", icon: "activity", admin: true },
];

// On a phone, four: the rest are under «Más».
const TABS = [
  { value: "inicio", label: "Inicio", icon: "home" },
  { value: "bots", label: "Bots", icon: "bot" },
  { value: "contactos", label: "Contactos", icon: "message-circle" },
  { value: "mas", label: "Más", icon: "sliders" },
];

export function sectionsFor(admin) {
  return SECTIONS.filter((section) => admin || !section.admin);
}

export function go(path) {
  window.location.hash = path;
}

// --- The theme ----------------------------------------------------------------------------------------------------

const THEME_KEY = "hive-theme";

export function savedTheme() {
  try {
    return window.localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
  } catch (error) {
    // Storage may be closed (a private window): the default stands.
    return "dark";
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch (error) {
    // Not remembered, but applied: it lasts until the page is loaded again.
  }
}

function ThemeButton({ theme, onTheme }) {
  const next = theme === "dark" ? "light" : "dark";
  return html`<${Button} variant="ghost" size="sm" onClick=${() => onTheme(next)}>
    ${next === "light" ? "Tema claro" : "Tema oscuro"}<//>`;
}

// --- The side bar and the phone's bar -------------------------------------------------------------------------------

// The marks of assets/logos (its README): the lockup for the theme's ground, the mark alone where there is no room.
// The hexagon of `Cell` names bots and clients; it is not the logo.
export function lockup(theme, stacked = false) {
  return `/logos/hive-logo-${stacked ? "stacked-" : ""}${theme === "light" ? "light" : "dark"}.svg`;
}

function shortId(id) {
  return id && id.length > 12 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id || "";
}

function ClientCard({ who, client, theme, onTheme }) {
  const name = (client && client.name) || "Tu cliente";
  return html`
    <div class="pwa-client">
      <div class="pwa-client-who">
        <${Cell} name=${name} size=${32} tone="ash" />
        <div class="pwa-client-names">
          <span class="pwa-client-name">${name}</span>
          <span class="mono pwa-muted" title=${who.client}>${shortId(who.client)}</span>
        </div>
      </div>
      <div class="pwa-client-actions">
        <${Badge} tone=${who.role === "cliente-admin" ? "amber" : "info"} dot=${false}>${ROLES[who.role] || who.role}<//>
        <${Button} variant="ghost" size="sm" onClick=${signOut}>Salir<//>
      </div>
      <${ThemeButton} theme=${theme} onTheme=${onTheme} />
    </div>`;
}

function SideBar({ who, client, admin, section, theme, onTheme }) {
  return html`
    <aside class="pwa-sidebar">
      <a class="pwa-brand" href="#/" aria-label="Hive, inicio">
        <img class="pwa-logo" src=${lockup(theme)} alt="" width="132" height="42" />
        <span class="mono pwa-muted pwa-brand-tag">PWA</span>
      </a>
      <nav class="pwa-nav" aria-label="Área del cliente">
        ${sectionsFor(admin).map((each) => html`
          <a key=${each.key} class=${`pwa-nav-item${each.key === section ? " on" : ""}`} href=${`#/${each.key}`}
              aria-current=${each.key === section ? "page" : undefined}>
            <${Icon} name=${each.icon} size=${20} />${each.label}
          </a>`)}
      </nav>
      <${ClientCard} who=${who} client=${client} theme=${theme} onTheme=${onTheme} />
    </aside>`;
}

function PhoneBar({ client, section }) {
  const current = TABS.some((tab) => tab.value === section) ? section : "mas";
  return html`
    <header class="pwa-phonebar">
      <a class="pwa-brand" href="#/" aria-label="Hive, inicio">
        <img class="pwa-mark" src="/logos/hive-mark.svg" alt="" width="28" height="28" /><span>Hive</span>
      </a>
      <span class="mono pwa-muted pwa-phonebar-client">${(client && client.name) || ""}</span>
    </header>
    <footer class="pwa-tabbar">
      <${TabBar} items=${TABS} value=${current} onChange=${(value) => go(`/${value}`)} />
    </footer>`;
}

export function Shell({ who, client, admin, section, theme, onTheme, children }) {
  return html`
    <div class="pwa-shell">
      <${SideBar} who=${who} client=${client} admin=${admin} section=${section} theme=${theme} onTheme=${onTheme} />
      <${PhoneBar} client=${client} section=${section} />
      <main class="pwa-main">${children}</main>
    </div>`;
}

// --- A page -----------------------------------------------------------------------------------------------------------

// The header of a page: where it is (the breadcrumb), the client as its eyebrow, its title and at most one primary
// action, in amber.
export function PageHeader({ client, crumbs = [], title, subtitle, action }) {
  return html`
    <header class="pwa-page-header">
      <div class="pwa-page-titles">
        ${crumbs.length > 0 && html`
          <nav class="pwa-crumbs" aria-label="Ruta">
            ${crumbs.map((crumb, at) => html`<span key=${at}>${at > 0 && html`<span aria-hidden="true"> / </span>`}${
              crumb.href ? html`<a href=${crumb.href}>${crumb.label}</a>` : html`<span aria-current="page">${crumb.label}</span>`}</span>`)}
          </nav>`}
        <span class="pwa-eyebrow">${(client && client.name) || "Hive"}</span>
        <h1 class="heading-lg">${title}</h1>
        ${subtitle && html`<span class="pwa-muted">${subtitle}</span>`}
      </div>
      ${action && html`<div class="pwa-page-action">${action}</div>`}
    </header>`;
}

// The call a screen makes, as an integration would make it (ADR-0027, code first).
export function PageCall({ title, method, route, body }) {
  return html`<div class="pwa-page-call">
    <${CodeSnippet} title=${title} variants=${[{ lang: "bash", label: "cURL", code: curl(method, route, body) }]} /></div>`;
}

// A section the PWA does not have yet: what it will hold, and its call to hive-api meanwhile when there is one.
const PENDING = {
  inicio: { title: "Visión general", text: "Las métricas de las últimas 24 horas y el estado de cada bot." },
  contactos: { title: "Contactos", text: "Buscar a una persona en todos los bots del cliente.",
    call: { method: "POST", route: "contacts/search", body: { waId: "<número>" } } },
  plantillas: { title: "Plantillas", text: "Las plantillas de Meta de todos los bots. Hoy están en la ficha de cada bot." },
  personas: { title: "Personas", text: "Invitar personas al cliente, cambiar su rol o darlas de baja.",
    call: { method: "GET", route: "people" } },
  credenciales: { title: "Credenciales", text: "Las claves de firma de la credencial de máquina del cliente.",
    call: { method: "GET", route: "keys" } },
  integraciones: { title: "Integraciones", text: "Los servicios de terceros conectados a la cuenta del cliente." },
  auditoria: { title: "Auditoría", text: "Lo que pasó en el cliente: quién cambió qué y cuándo." },
};

export function Pending({ client, section }) {
  const pending = PENDING[section];
  return html`
    <section aria-label=${pending.title}>
      <${PageHeader} client=${client} title=${pending.title} />
      <${EmptyState} title="Todavía no está en la PWA" actionLabel=${section === "plantillas" ? "Ir a los bots" : undefined}
          onAction=${() => go("/bots")}>${pending.text}<//>
      ${pending.call && html`<${PageCall} title=${`${pending.title}, por la API`} ...${pending.call} />`}
    </section>`;
}

export function isPending(section) {
  return section in PENDING;
}

// «Más», on a phone: the sections the TabBar has no room for, the theme and the way out.
export function More({ who, client, admin, theme, onTheme }) {
  const shown = new Set(TABS.map((tab) => tab.value));
  return html`
    <section aria-label="Más">
      <${PageHeader} client=${client} title="Más" />
      <nav class="pwa-more" aria-label="Más secciones">
        ${sectionsFor(admin).filter((each) => !shown.has(each.key)).map((each) => html`
          <a key=${each.key} class="pwa-nav-item" href=${`#/${each.key}`}><${Icon} name=${each.icon} size=${20} />${each.label}</a>`)}
      </nav>
      <${ClientCard} who=${who} client=${client} theme=${theme} onTheme=${onTheme} />
    </section>`;
}
