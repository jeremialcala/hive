# ADR-0027: El mapa de estilo de Hive — Pragmatismo Técnico, oscuro por defecto, y dónde se registra

* **Estado:** accepted
* **Fecha:** 2026-10-26
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 02-design
* **Versión:** 0.1.0
* **ID:** ADR-0027
* **Supersede:** —
* **Controles OWASP afectados:** A02 (las credenciales de Meta y las claves se ofuscan al guardarse y no vuelven a
  mostrarse), A04 (salvaguardas de diseño: toda acción destructiva exige escribir el nombre o el UUID del recurso),
  A05 (las superficies no cargan fuentes ni scripts de fuera; la CSP sigue la línea de la ADR-0026)

Decisiones de Jeremi del 2026-10-26: el estilo de las tres superficies de Hive, su logotipo y el lugar del repo donde
quedan registrados.

## Contexto

Hive tiene nombre (ADR-0024) y una API documentada (ADR-0026), pero ninguna superficie visual todavía. El producto
necesita tres: una **landing** de entrada en `hive.higerotech.com`, el **área del cliente** (la PWA con la que cada
cliente gestiona sus bots) y el **área del operador** (el portal interno de administración y segundo nivel). Las tres
deben verse como un solo producto, y quien las construya, persona o agente, necesita un punto único de verdad para
colores, tipografía, componentes y voz.

Un primer mapa de estilo (paleta cálida, tipografía display) se descartó el mismo día por un brief de Jeremi que fija
el concepto y la paleta.

## Decisión

1. **El concepto es "Pragmatismo Técnico":** una interfaz que transmite confianza, velocidad y control sobre los
   datos, sin elementos decorativos. El tema **oscuro es el de defecto** (minimiza la fatiga de quien gestiona logs y
   configuraciones extensas); el claro existe para la landing y para quien lo elija.
2. **La paleta** es la del brief, con nombre propio en los tokens: Slate Black `#0F1115` (fondo), Dark Ash
   `#161B22` (paneles), Hive Amber `#FFC837` (acento: acción primaria y lo activo, siempre con tinta oscura encima),
   Meta Blue `#0668E1` y WhatsApp Green `#25D366` solo para identificar canales, Success Green `#10B981`, Error Red
   `#EF4444`. Se añaden un naranja de advertencia y un azul de información porque el brief no los cubría. Ningún estado
   se comunica solo con color.
3. **La tipografía:** Geist (Inter de respaldo) para la interfaz, con el cuerpo a 14px para pantallas densas;
   JetBrains Mono (Fira Code de respaldo) obligatoria para tokens, firmas, UUIDs, `waId`/`fbId`, payloads y
   fragmentos de código. **Las fuentes se sirven desde el propio origen**, vendorizadas (licencia SIL OFL), nunca
   desde Google Fonts: la CSP de las superficies no deja nada de fuera, como la de la página de documentación.
4. **Cuatro patrones de interacción** son obligatorios en las superficies: *code-first* (toda pantalla que genere
   claves, `verifyToken` o la URL del webhook entrega un snippet cURL/Python listo para probar); *salvaguardas*
   (`DELETE /bot/{bot_id}`, revocar un cliente o borrar un contacto exigen escribir el nombre o el UUID exacto en un
   modal; nunca un `confirm()` del navegador); *cargas deterministas* (skeletons o spinner con el micro-copy técnico
   de lo que se espera, "Esperando confirmación del bus de eventos…", nunca "Cargando" a secas); *slide-overs* para
   editar un intent, revisar un contacto o rotar una clave sin perder la tabla.
5. **El onboarding de un bot son cuatro pasos lineales:** Identidad → Motor de reglas → Cifrado y credenciales
   (`facebookToken`, `whatsappToken`, `appSecret`, que se guardan cifrados con la clave de datos del cliente, la que
   envuelve Andvari, y no vuelven a mostrarse) →
   Go-Live, donde la interfaz muestra el `verifyToken` y `https://hive-webhook.higerotech.com/chat/{bot_id}` y queda
   escuchando hasta el primer `GET /chat/{bot_id}` exitoso desde Meta, que marca el bot como ACT.
6. **La navegación mapea los dominios de `hive-api`,** separando la vista del cliente (Visión general, Bots,
   Cerebro e intents, Audiencia y contactos, Credenciales) de la del operador (Clientes, Service keys, Trazas y
   eventos). Los estados del bot se muestran tal como los devuelve la API: REG, ACT, ERR.
7. **El logotipo** es un hexágono isométrico (la celda vista como cubo) con tres anillos, radios, el eje del cubo y
   nodos, en Hive Amber con el anillo intermedio gris y nodos verdes; el wordmark HIVE está trazado en geometría
   propia, sin depender de una fuente. El motivo de la colmena es la celda hexagonal; no hay abejas ni rayas. No se
   usan los logotipos de Meta, Messenger, WhatsApp ni Instagram: los canales se identifican por su inicial sobre su
   color.
8. **Dónde vive y dónde se registra.** La fuente de verdad es el artifact "Hive" de tipo Design System en Claude
   (tokens, componentes con vista previa, brand book, logos). El repo guarda una **exportación versionada** en
   `assets/design-system/` y los logos en `assets/logos/`, y el registro con el historial de versiones y el
   procedimiento de actualización en `docs/02-design/design-system.md`. Nada de la exportación se edita a mano: un
   cambio de estilo va primero al artifact y se registra como ADR si es una decisión.

## Alternativas consideradas

| Opción | Pros | Contras |
|---|---|---|
| **Design system en el artifact + exportación versionada en el repo** | Vistas previas vivas y editables; el repo tiene una copia reproducible que el CI y los agentes pueden leer sin acceso al artifact | Dos copias: hay que exportar tras cada cambio |
| Solo en el repo (tokens y CSS a mano) | Una sola copia | Sin vistas previas ni brand book navegable; más fácil que el estilo se desvíe en cada superficie |
| Solo en el artifact | Una sola copia, siempre al día | El código no puede leerlo sin acceso a Claude; nada queda en el historial de git |

| Fuentes | Pros | Contras |
|---|---|---|
| **Vendorizadas en cada superficie** | CSP sin nada de fuera; sin dependencia de Google; mismas fuentes offline en la PWA | Pesan en la imagen; hay que actualizarlas a mano |
| Google Fonts | Cero mantenimiento | Rompe la CSP de la ADR-0026; una petición a Google por cada visita |

## Consecuencias

- Las superficies se construyen con `tokens.css`, `bundle.css` y los componentes de `assets/design-system/`; un token
  o un componente que no exista allí se añade primero al design system.
- Cada superficie incluye las fuentes en su propia imagen y su CSP las declara `self`.
- `hive-app-icon.svg` y `hive-app-icon-maskable.svg` se exportan a PNG 192/512 para el manifest de la PWA, con
  `theme_color` y `background_color` en `#0F1115`.
- El brief original queda recogido en el brand book exportado; los documentos de diseño siguen diciendo «el
  gateway» y «el motor» (ADR-0024).

## Condiciones de revisión

- Hive pasa a tener dominio o marca registrada propios (ADR-0024): se revisa el wordmark.
- Se añade una cuarta superficie (por ejemplo, una app nativa) con restricciones que los tokens no cubran.
- Aparece una herramienta que permita mantener el design system directamente en el repo con vistas previas.
