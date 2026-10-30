Hive es la plataforma que permite a una empresa crear, desplegar y auditar bots con IA para Messenger, WhatsApp e Instagram, y la consola con la que el operador de la plataforma administra tenants, claves de servicio y el flujo de eventos. El concepto visual es **Pragmatismo Técnico**: una interfaz que transmite confianza, velocidad y control total sobre los datos, sin elementos decorativos. Tres superficies comparten este sistema: la **landing** (entrada), el **área del cliente** (PWA de autogestión del tenant) y el **área del operador** (portal interno, segundo nivel). La sección *Superficies y arquitectura* detalla el sitemap y qué cambia en cada una.

## Principios

- **Pragmatismo técnico.** Cada pantalla existe para operar algo: ver un estado, cambiar una configuración, probar una integración. Si un elemento no informa ni permite actuar, sobra.
- **Oscuro por defecto.** El tema `dark` es el primero y el de respaldo: minimiza la fatiga de quien gestiona logs y configuraciones extensas. El tema `light` existe para la landing y para quien lo elija en Ajustes, con los mismos acentos.
- **Code-first.** Donde se genera una clave, un token de verificación o una URL de webhook, la UI entrega un `CodeSnippet` listo para copiar (cURL o Python) para probar la integración en el momento.
- **Sin clics accidentales.** Toda acción destructiva (`DELETE /bot/{bot_id}`, revocar un cliente, borrar un contacto) pasa por `ConfirmDestructive`: el usuario escribe el nombre o UUID exacto del recurso.
- **Carga determinista.** Nada dice solo "Cargando". `LoadingState` muestra skeletons o un spinner con el micro-copy técnico de lo que se espera ("Esperando confirmación del bus de eventos…").
- **Contexto que no se pierde.** Editar un intent, revisar un contacto o rotar una clave ocurre en un `SlideOver` desde la derecha, con la tabla principal visible detrás.
- **Secretos que no vuelven a verse.** `facebookToken`, `whatsappToken`, `appSecret` y claves privadas se ofuscan al guardarse (`Field secret`). La UI dice dónde viven: cifrados, en Andvari.

## Voz y contenido

Hive escribe en español neutro de LATAM y trata al usuario de **tú**. Frases cortas, verbo primero, sin signos de exclamación y sin emoji. El vocabulario sigue a la `hive-api`:

- **bot** (no "chatbot" ni "agente"), **cliente** o **tenant** (en el área del operador), **contacto** (la persona que escribe, identificada por `waId` o `fbId`), **vuelta** (cada intercambio de una conversación), **intent**, **regla**, **System Prompt**, **herramienta** (acción que el LLM puede ejecutar).
- Estados del bot, tal como los devuelve la API y siempre en `key` mayúsculas: **REG** (registrado, sin conectar), **ACT** (activo, Meta verificó el webhook), **ERR** (error de firma, token o bus). `Badge state` los traduce al lado: "REG · Registrado".
- Verbos de acción fijos: *crear bot*, *desplegar reglas*, *rotar clave*, *revocar cliente*, *exportar datos*, *borrar definitivamente*. El botón dice el verbo; el toast confirma en participio ("Reglas desplegadas").
- Infraestructura con nombre propio, siempre en mayúscula inicial y sin traducir: **Bifröst** (bus de eventos), **Andvari** (bóveda de secretos), **hive-coordinator** y **hive-responder** (motor), **EventAction**.
- Los errores dicen qué falló y qué hacer, con el detalle técnico en `key`: "Meta rechazó la firma HMAC-SHA256 del webhook. Revisa `appSecret` y vuelve a verificar."
- Cifras con espacio fino de miles (`1 284`) en `data-lg` o `key`, siempre con `font-variant-numeric: tabular-nums`.
- Se quedan en inglés los términos que el usuario técnico ya conoce: webhook, token, intent, payload, System Prompt, sync, rollback. Todo lo demás se traduce.

Copy real del producto:

> **Hero:** Control total sobre tus bots. — Crea, despliega y audita bots con IA para Messenger, WhatsApp e Instagram, con cada evento trazable.
>
> **Go-Live:** Escuchando el primer `GET /chat/{bot_id}` desde Meta… Cuando llegue, el bot pasará a ACT.
>
> **Confirmación destructiva:** Vas a borrar el bot *Soporte Nocturno* y sus 3 102 vueltas. Escribe `Soporte Nocturno` para confirmar.

## Color

Dos temas, `dark` primero. La jerarquía de superficies es por nivel: `slate-black` (fondo base), `dark-ash` (paneles superpuestos: tarjetas, barra lateral, slide-over) y `dark-ash-raised` (campos, cabeceras de tabla, bloques de código, skeletons). Entre bloques, `border`; alrededor de controles, `border-strong`.

- Texto: `ink` para contenido, `ink-muted` para metadatos, `ink-faint` solo deshabilitado. Cada nota de token nombra los fondos donde cumple 4.5:1 en ambos temas.
- Acento: `hive-amber` guía el ojo a la acción principal (Crear bot, Desplegar reglas) y marca lo activo: paso del wizard, pestaña, fila seleccionada (`amber-soft`), indicador de escucha. Encima del ámbar siempre `on-amber`; nunca blanco. En oscuro el ámbar también es texto (`amber-text`); en claro `amber-text` es un ámbar oscuro porque #ffc837 sobre blanco no llega a 2:1.
- Canales: `meta-blue` para Messenger e Instagram y `whatsapp-green` para WhatsApp, como relleno de la marca del `ChannelChip` (inicial encima en `on-channel` sobre azul, `on-amber` sobre verde) y como `*-text` para el nombre. Son colores de identificación, no de estado: `success-green` y `whatsapp-green` nunca se intercambian.
- Estado: `success-green`/`success-text`/`success-soft` para webhook verificado, token activo y bot ACT; `error-red`/`error-text`/`error-soft` para firma inválida, desconexión de Bifröst y bot ERR; `warn` y `info` son añadidos al brief para cola con retraso y procesos en curso. Un estado nunca va solo en color: `Badge` lleva código y palabra.
- Foco de teclado: `focus-ring` como anillo de 2px separado del control por 2px de `dark-ash` (`box-shadow: 0 0 0 2px var(--dark-ash), 0 0 0 4px var(--focus-ring)`).
- Prohibido: degradados, ámbar como advertencia, blanco sobre ámbar o sobre rojo, bordes de color a la izquierda de las tarjetas, sombras para separar paneles.

## Tipografía

Dos familias de Google Fonts, sin archivos propios (carga `Geist`, `Inter` y `JetBrains Mono` en una sola hoja `css2`; Inter es el respaldo de Geist).

- **UI y textos · Geist / Inter** (`heading-xl` a `heading-sm`, `body`, `body-strong`, `body-lg`, `label`, `caption`). El cuerpo de toda la interfaz es `body` 14/20, pensado para pantallas densas; la landing usa `body-lg`. Los títulos son la misma familia en 600/700 con tracking negativo: Hive no tiene tipografía display.
- **Datos críticos y logs · JetBrains Mono / Fira Code** (`code`, `key`, `data-lg`). Obligatorio para tokens JWT, firmas, UUIDs de eventos, `waId`/`fbId`, JSON de payloads, nombres de cola, timestamps y fragmentos de código. Si un operador podría copiarlo o compararlo, va en mono.

Ancho de lectura: `reading-max` para formularios, wizard y texto corrido. Títulos con `text-wrap: balance`.

## Espaciado, radios y elevación

Escala de 4px (`space-1` a `space-8`); compón con `gap`. El área del cliente y del operador respiran en `space-3`/`space-4`; la landing en `space-7`/`space-8`.

Radios contenidos: `radius-sm` en badges y chips, `radius-md` en botones, campos, código y tarjetas, `radius-lg` solo en modales, slide-overs y MetricCard, `radius-pill` en chips de canal e interruptores. `Cell` se recorta en hexágono sin radio.

Elevación por nivel de superficie, no por sombra. `shadow-menu` para menús y tooltips; `shadow-overlay` para slide-over y modal. Nada más proyecta sombra.

## Iconografía

Iconos **Lucide** (trazo 1.5px; 20px en botones y navegación, 16px en badges, tablas y chips). Nunca emoji. Color: el del texto que acompañan (`currentColor`).

Canales: se usan los colores `meta-blue` y `whatsapp-green` del brief, pero no los logotipos de Meta, Messenger, WhatsApp ni Instagram (marcas de terceros): `ChannelChip` muestra la inicial sobre el color del canal.

## Logotipo

La marca es un hexágono isométrico: la celda vista como cubo, con tres anillos (ámbar, gris, ámbar), radios en las seis aristas, el eje en Y del cubo, nodos ámbar en los anillos y dos nodos verdes en los lados. Lee como colmena, circuito y contenedor a la vez. Los archivos están en Assets → Logos y la vista previa *Logo* en Componentes los muestra sobre ambos fondos.

- Isotipo `hive-mark.svg` para cabecera móvil, avatar de cuenta y documentos; funciona sobre `slate-black` y sobre blanco.
- Lockup horizontal `hive-logo-dark.svg` sobre `slate-black` y `dark-ash`; `hive-logo-light.svg` sobre fondos claros. Lockup vertical `hive-logo-stacked-*.svg` para login, splash de la PWA y portadas. Wordmark suelto en `hive-wordmark-*.svg`. El sufijo siempre nombra el fondo.
- Monocromos (`*-mono-amber`, `*-mono-dark`, `*-mono-light`) para fondos cargados, una tinta y co-branding.
- Tamaños pequeños: `hive-favicon.svg` (versión simplificada: anillo exterior, anillo interior, eje y nodos verdes) y los iconos de la PWA `hive-app-icon.svg` (`any`), `hive-app-icon-maskable.svg` (`maskable`) y `hive-app-icon-light.svg`, con `theme_color` y `background_color` en `slate-black`.
- Tintas: `hive-amber` (anillos, radios, nodos), `ink-muted` oscuro (anillo intermedio), `success-green` (segmentos y nodos laterales: los nodos "activos"), núcleos en `slate-black`.
- El wordmark HIVE está trazado en geometría propia (cap 100, trazo 20, uniones redondeadas); no lo rehagas con Geist. El nombre en texto corriente sigue siendo "Hive" en `sans` 700.
- Área de respeto: la mitad del alto del isotipo por lado; mínimo 24px el isotipo y 120px de ancho el lockup. No recolorear, no girar, no poner ámbar detrás. El hexágono de `Cell` identifica bots y tenants; no es el logo.

## Movimiento y estados

- Transiciones de 120ms `ease-out` en hover y foco; 200ms para slide-over y modal (entra desde la derecha; el scrim aparece en opacidad). Nada se anima al cargar.
- `prefers-reduced-motion`: sin desplazamientos, solo opacidad; el spinner se sustituye por texto.
- Estados de control: reposo → hover (`dark-ash-raised`, o ámbar un 8% más oscuro) → activo → foco (`focus-ring`) → deshabilitado (`ink-faint` sobre `dark-ash-raised`).
- Carga: `LoadingState` con skeletons en `dark-ash-raised` para listas y tarjetas; spinner con micro-copy técnico para operaciones que dependen del bus (NATS JetStream o RabbitMQ). Un `GoLive` que espera a Meta pulsa en ámbar hasta recibir el primer `GET`.

## Uso del bundle

Los componentes viven en `components/bundle.js` como `window.Hive` y esperan React 18 y ReactDOM 18 en la página. Carga `tokens.css`, las fuentes de Google, `components/bundle.css` y por último el bundle. Cada componente tiene su guía en `components/<Nombre>/README.md` con lo que el consumidor aporta y sus prohibiciones.
