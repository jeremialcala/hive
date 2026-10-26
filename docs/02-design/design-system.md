# Design system de Hive — registro

* **Estado:** approved
* **Fecha:** 2026-10-26
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ADRs relacionadas:** ADR-0024 (el producto se llama Hive), ADR-0026 (CSP sin nada de fuera), ADR-0027 (el mapa de estilo)

Este documento es el registro del design system de Hive: dónde vive, qué versión está exportada al repo, cómo se
actualiza y cómo lo consumen las superficies. Las decisiones de estilo están en la
[ADR-0027](../00-project/adr/0027-el-mapa-de-estilo-de-hive.md); los archivos, en
[`assets/design-system/`](../../assets/design-system/) y [`assets/logos/`](../../assets/logos/).

## Dónde vive

| Qué | Dónde |
|---|---|
| Fuente de verdad (tokens, componentes con vista previa, brand book, logos) | Artifact "Hive" de tipo Design System en Claude: `https://claude.ai/artifact/GmvG1HC13Tw894s9RxyedM` (privado; lo comparte Jeremi) |
| Exportación versionada para el código | `assets/design-system/` (tokens, bundle, tipos, brand book, superficies) |
| Logotipo | `assets/logos/` con su `README.md` de uso |
| Decisiones | ADR-0027 y las que la sigan, una por decisión |

Un agente que necesite el estilo lee `assets/design-system/brand-book.md` y `tokens.json`; si tiene acceso al
artifact, lee su `project/README.md`, que es el mismo brand book con las vistas previas.

## Qué cubre

Tres superficies con una sola identidad ([`superficies.md`](../../assets/design-system/superficies.md)):

| Superficie | Quién | Dónde se sirve |
|---|---|---|
| Landing (entrada) | Empresa que evalúa Hive | `hive.higerotech.com` (reservado en ADR-0024) |
| Área del cliente (tenant), PWA de autogestión | Gestor de bots del cliente | Portal sobre `hive-api` |
| Área del operador (admin/root), portal interno | Operador de la plataforma | Portal sobre `hive-api`, solo operador |

La navegación mapea los dominios de `hive-api`: Visión general, Bots (`/bot`), Cerebro e intents
(`/chat/{bot_id}/rules` e `/intents`), Audiencia y contactos (`/chat/{bot_id}/contacts` y `/contacts/search`),
Credenciales (`/keys`); y para el operador Clientes (`/clients`), Service keys
(`/clients/{client_id}/service-keys`) y Trazas y eventos, que todavía no tiene ruta en `hive-api`.

Dos cosas que las superficies dan por hechas y la API todavía no hace:
- la ficha del bot (`GET /chat/{bot_id}/info`) no devuelve su estado (REG, ACT, ERR);
- la verificación de Meta (`GET /chat/{bot_id}`) no pasa el bot a ACT, y el Go-Live del onboarding la espera.

## Versiones exportadas

| Versión del artifact | Fecha | Qué trae | Exportada al repo |
|---|---|---|---|
| v1 | 2026-10-26 | Primer mapa de estilo (paleta cálida, Bricolage/Figtree); descartado por el brief | — |
| v3 | 2026-10-26 | Brief "Pragmatismo Técnico": oscuro por defecto, Slate Black / Dark Ash / Hive Amber, Geist + JetBrains Mono, sitemap Cliente/Operador, 20 componentes | — |
| v4 | 2026-10-26 | Logotipo: isotipo hexagonal isométrico, wordmark geométrico, lockups | — |
| **v5** | 2026-10-26 | Variantes del logotipo: monocromos, favicon, iconos de app (PWA) y lockup vertical | **Sí** (`assets/design-system/`, `assets/logos/`) |

## Cómo se actualiza

1. El cambio se hace en el artifact (tokens, componentes, brand book o logos). Una decisión de estilo nueva se
   registra como ADR, una por decisión.
2. Se exportan entero `project/tokens.json`, `project/components/bundle.css`, `bundle.js`, `index.d.ts`,
   `project/README.md` (como `brand-book.md`) y `project/superficies.md` a `assets/design-system/`; se regenera
   `tokens.css` con la misma disposición que compila la página (`:root` + `[data-theme]`, una clase por estilo).
   Los SVG de `project/assets/Logos/` van a `assets/logos/`.
3. Se añade la fila a la tabla de versiones y la entrada al `CHANGELOG.md`.
4. Nada de la exportación se edita a mano: si el código necesita algo distinto, cambia primero el artifact.

## Cómo lo consumen las superficies

- Orden de carga: `tokens.css` → fuentes → `bundle.css` → React 18 → `bundle.js` (`window.Hive`).
- Tema: `dark` es el primero y el de respaldo; `<html data-theme="light">` activa el claro.
- Fuentes desde el propio origen, nunca desde Google Fonts: la CSP de las superficies no deja nada de fuera
  (ADR-0026, ADR-0027). Geist, Inter y JetBrains Mono tienen licencia SIL OFL y se vendorizan en cada superficie.
- Las credenciales de Meta se muestran con `Field secret`; tras guardarse no vuelven a verse (ADR-0007, ADR-0027).
- Toda acción destructiva pasa por `ConfirmDestructive`, escribiendo el nombre o el UUID del recurso.
- Un contrato de UI se prueba como los de API: si una superficie usa un token o un componente que no existe en la
  exportación, el cambio va primero al design system.

## Pendiente

- Vendorizar las fuentes en cada superficie cuando exista la primera (hoy no hay front-end en el repo).
- Exportar `hive-app-icon.svg` y `hive-app-icon-maskable.svg` a PNG 192/512 para el manifest de la PWA.
- Copiar los iconos Lucide que usa el sistema como assets propios.
- Corregir en el artifact, para la próxima exportación:
  - el brand book dice que los secretos viven «cifrados, en Andvari», cuando están en MongoDB, cifrados con la clave
    de datos del cliente, y Andvari solo guarda la clave maestra que la envuelve;
  - `superficies.md` nombra `/events`, una ruta que `hive-api` no tiene.
- La landing en `hive.higerotech.com`: hoy `/` redirige a `/docs/` (ADR-0026), y la landing ocupará esa ruta.
