# Logos

Marca gráfica de Hive: un hexágono isométrico (la celda vista como cubo) con tres anillos, radios en las seis aristas, el eje en Y del cubo, nodos en los anillos y dos nodos verdes en los lados. Dibujada con los tokens del sistema y sin dependencia de fuentes: el wordmark HIVE está construido con trazos geométricos (cap de 100, trazo 20, uniones redondeadas), no con Geist.

Convención de nombres: el sufijo dice el **fondo** sobre el que va. `-dark` = sobre `slate-black` / `dark-ash`; `-light` = sobre blanco o `dark-ash` del tema claro. Sin sufijo = sirve en ambos.

## A color (uso principal)

| Archivo | Uso |
|---|---|
| `hive-mark.svg` | Isotipo. Cabecera móvil, avatar de cuenta, documentos. Ambos fondos. |
| `hive-logo-dark.svg` / `hive-logo-light.svg` | Lockup horizontal: barra superior de la landing, cabecera del área del cliente y del operador, pie. |
| `hive-logo-stacked-dark.svg` / `-light.svg` | Lockup vertical: pantalla de login, splash de la PWA, portada de documentos, redes. |
| `hive-wordmark-dark.svg` / `-light.svg` | Solo HIVE, cuando el isotipo ya está en pantalla (pie de la landing). |

## Monocromo (una sola tinta)

Para fondos cargados, impresión a una tinta, marcas de agua y co-branding donde el color de otros compite. El anillo gris y los acentos verdes pasan a la misma tinta; los núcleos de los nodos toman el color del fondo.

| Archivo | Tinta |
|---|---|
| `hive-mark-mono-amber.svg` / `hive-logo-mono-amber.svg` | `hive-amber`; solo sobre fondos oscuros. |
| `hive-mark-mono-dark.svg` / `hive-logo-mono-dark.svg` | `ink` del tema oscuro (#e6edf3) sobre fondos oscuros. |
| `hive-mark-mono-light.svg` / `hive-logo-mono-light.svg` | #0f1115 sobre fondos claros. |

## Favicon e iconos de app

Versión simplificada para tamaños pequeños: solo anillo exterior (más grueso), anillo interior, eje en Y y los dos nodos verdes; sin anillo gris, radios ni nodos pequeños, que a 16px se convierten en ruido.

| Archivo | Uso |
|---|---|
| `hive-favicon.svg` | `<link rel="icon">`; caja de 32, legible a 16. |
| `hive-favicon-mono-dark.svg` / `-light.svg` | Favicon monocromo para pestañas ancladas y barras del sistema. |
| `hive-app-icon.svg` | Icono de la PWA, 512×512, fondo `slate-black` con radio 22 %, isotipo al 66 %. `purpose: "any"`. |
| `hive-app-icon-maskable.svg` | Igual, isotipo al 52 % dentro de la zona segura del 80 %. `purpose: "maskable"`. |
| `hive-app-icon-light.svg` | Icono con fondo blanco para tiendas o contextos que exigen fondo claro. |

Manifest de la PWA: `theme_color` y `background_color` = `#0f1115`; exporta los SVG a PNG en 192 y 512 para `icons[]`.

## Tintas

- Anillos exterior e interior, radios, eje y nodos: `hive-amber` #ffc837.
- Anillo intermedio: `ink-muted` oscuro #8b949e (gris técnico; en claro sigue siendo gris medio a propósito).
- Segmentos de circuito y nodos laterales: `success-green` #10b981 (los nodos "activos", como un bot ACT).
- Núcleo de los nodos: `slate-black` #0f1115 sobre oscuro; blanco en los monocromos para fondo claro.

## Reglas

- Área de respeto: la mitad del alto del isotipo por cada lado. Tamaño mínimo: isotipo completo 24px, favicon 16px, lockup horizontal 120px de ancho, lockup vertical 96px de ancho.
- No recolorear fuera de las variantes de esta carpeta, no girar (apunta hacia arriba, como `Cell`), no poner ámbar detrás del isotipo, no mezclar el isotipo a color con el wordmark monocromo.
- No reconstruir el wordmark con texto en Geist dentro de la interfaz: usa el SVG. El nombre escrito en texto corriente sigue siendo "Hive" en `sans` 700.
