# Design system de Hive — exportación

Copia versionada de los archivos del design system de Hive. La fuente de verdad es el artifact
"Hive" (Design System) de Claude; el registro, su historial y el procedimiento de actualización están en
[`docs/02-design/design-system.md`](../../docs/02-design/design-system.md).

**No se edita a mano.** Un cambio de estilo se hace en el artifact y se vuelve a exportar aquí entero, con la
versión y la fecha en la tabla del registro.

| Archivo | Qué es | Quién lo consume |
|---|---|---|
| `tokens.json` | Los tokens: colores por tema (`dark` primero), tipografía, espaciado, radios, sombras y medidas de layout, cada uno con su nota de uso | Herramientas: generan `tokens.css` o los tokens de Tailwind |
| `tokens.css` | Los mismos tokens compilados como custom properties (`:root` y `[data-theme]`) y una clase por estilo de texto | Las tres superficies, en el `<head>`, antes de todo lo demás |
| `bundle.css` | Estilos de los componentes (`.hv-*`), construidos solo con tokens | Las superficies que usan los componentes |
| `bundle.js` | Los 20 componentes como `window.Hive` (React 18 UMD; sin `import`, sin red) | Prototipos y superficies sin build; las SPA pueden adaptarlo a módulos |
| `index.d.ts` | Las props de cada componente, como documentación | Quien implemente o consuma los componentes |
| `brand-book.md` | El brand book: principios, voz, color, tipografía, iconografía, logotipo, movimiento | Diseño, front-end y quien escriba copy |
| `superficies.md` | Sitemap y reglas por superficie: landing, área del cliente, área del operador | Producto y front-end |

Orden de carga: `tokens.css` → fuentes → `bundle.css` → React 18 → `bundle.js`.

Fuentes: el sistema nombra `Geist` (con `Inter` de respaldo) y `JetBrains Mono` (con `Fira Code`). En las
superficies de Hive se sirven desde el propio origen, no desde Google Fonts (ADR-0027, por la misma CSP de la
ADR-0026); los archivos de fuente no están en esta carpeta.

Snapshot: **v5 · 2026-10-26**.
