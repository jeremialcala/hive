# ADR-0026: La documentación de la API de gestión se publica aparte, en hive.higerotech.com/docs

* **Estado:** accepted
* **Fecha:** 2026-10-26
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 0.1.0
* **ID:** ADR-0026
* **Supersede:** —
* **Controles OWASP afectados:** A05 (la API sigue sin servir su esquema, y la página tiene una CSP sin nada de
  fuera), A06 (las dependencias de la página, fijadas y auditadas), A01 (la página no alcanza nada: solo sirve
  ficheros)

Decisiones de Jeremi del 2026-10-26: publicar la documentación aparte, en `hive.higerotech.com/docs`, y sin datos
de contacto ni términos de servicio.

## Contexto

La API de gestión no sirve `/docs`, `/redoc` ni `/openapi.json` fuera de desarrollo (`src/service.py`). Mostrarían
cada ruta y cada modelo a quien encuentre la dirección, desde el mismo proceso que guarda las credenciales de Meta.
Los contratos están en `docs/02-design/contracts/`, generados desde la aplicación, y una prueba falla si dejan de
coincidir con ella. Hasta hoy, quien quisiera la documentación tenía que leer el YAML del repositorio.

Al prepararla para publicarla, el contrato tenía cosas que no podían salir así:
- el título y el resumen eran los del proyecto de antes de Hive;
- la descripción explicaba la autenticación de antes de la ADR-0025;
- la de la ruta de reglas y la de la exportación de un contacto ya no eran ciertas;
- `info.version` decía `0.0.0`;
- llevaba como contacto el nombre, la web y el correo personal de Jeremi, y unos términos de web-ones.com.

## Decisión

1. **Una página estática, aparte de la API.** Un contenedor propio, `hive-docs`, sirve
   `https://hive.higerotech.com/docs`. La API sigue sin servir su esquema: la página se construye con el contrato
   versionado, así que publica lo mismo que la prueba de contrato garantiza.
2. **Redoc, generado con Redocly CLI.** Solo lectura y sin «probar la llamada». De todos modos no serviría: el token
   se pide con una aserción firmada y cifrada (ADR-0025), y eso no se escribe en un formulario.
3. **Nada de fuera.** Redocly CLI escribe una página que carga Redoc de `cdn.redocly.com` y las fuentes de Google.
   `deploy/docs/build.mjs` desactiva las fuentes y sirve Redoc desde la propia página, sacado del paquete npm de la
   misma versión. Antes comprueba que su hash es el que fija Redocly. La construcción falla si la página carga algo
   de fuera o tiene scripts en línea sin contar.
4. **Una CSP estricta:** `default-src 'none'`, scripts solo del propio sitio y los en línea por su hash, ningún
   formulario, base ni marco. Redoc necesita `style-src 'unsafe-inline'`, porque styled-components escribe sus
   `<style>` al vuelo, y `worker-src blob:` para la búsqueda.
5. **Donde todo Hive: en midgard, detrás de `hive-tunel`** (ADR-0024). nginx sin privilegios, sistema de ficheros de
   solo lectura, sin capacidades, solo en la red `publico`, 64 MB. La ubicación no necesita matriz: es un fichero
   estático sin coste ni carga, junto a la API que documenta, y con el túnel que ya existe.
6. **Sin contacto ni términos.** Decisión de Jeremi. Una prueba de contrato lo vigila: ni `contact` ni
   `termsOfService`, ninguna dirección de correo y nada de web-ones.com.
7. **El contrato dice dónde atiende cada API y de qué versión es.** El generador añade
   `servers: https://hive-api.higerotech.com`, o el del webhook, y pone en `info.version` la última versión del
   CHANGELOG, con `+develop` si hay cambios sin cortar. Lo añade al contrato, no a la aplicación: allí la
   documentación de desarrollo llamaría a producción.
8. **Las dependencias, como las de Python:** versiones exactas en `package.json`, y un `package-lock.json` resuelto
   con `npm install --before=<hace dos semanas>`, que respeta la regla de las dos semanas también en las 115
   transitivas. `npm ci --ignore-scripts` en la construcción y `npm audit` limpio. DOMPurify se fija en 3.4.17 con
   `overrides`, porque la 3.4.16 tenía un aviso crítico.

## Alternativas consideradas

- **Activar `/docs` en la propia API.** Descartada: el esquema lo serviría el mismo proceso que guarda las
  credenciales, y la página de FastAPI carga Swagger UI de un CDN.
- **Swagger UI.** Descartada: su gracia es probar las llamadas, y aquí no se pueden (punto 2).
- **Cloudflare Workers con assets estáticos o Pages.** Sería lo natural para un sitio estático, pero sacaría una
  pieza de Hive de midgard y del túnel, con otro despliegue y otras credenciales, para servir unos 1,5 MB.
- **La página de Redocly tal cual, con su CDN.** Descartada: cada visita dependería de cdn.redocly.com y de Google, y
  la CSP tendría que abrirles la puerta.
- **Publicar también el contrato del webhook.** Descartada: solo lo llama Meta, que no lo lee.

## Consecuencias

- La documentación pública es el contrato versionado: un cambio en la API cambia el contrato (la prueba lo exige), y
  hay que reconstruir `hive-docs` para que se vea.
- El túnel necesita un nombre público más, `hive.higerotech.com` hacia `http://hive-docs:8080`. Se da de alta en el
  panel de Cloudflare.
- `/` redirige a `/docs/`, y cualquier otra ruta devuelve 404: el resto del dominio queda libre.
- El navegador ejecuta el DOMPurify 3.4.13 que va dentro de `redoc.standalone.js` 2.5.4. Su aviso crítico pide HTML de
  un atacante, y aquí todo el contenido es el de nuestro contrato, fijado al construir la imagen. Además, la CSP no
  deja ejecutar en línea más que el script contado. Se revisa con la siguiente versión de Redocly CLI.
- La consola del navegador muestra los errores 418 y 423 de React: el HTML que Redocly CLI genera por adelantado no
  coincide con el que pinta Redoc 2.5.4, y React lo vuelve a pintar. Pasa igual con la página de Redocly sin tocar,
  cargada de su CDN. No afecta a lo que se ve.
- `hive-vigia` vigila también `hive-docs`.

## Condiciones de revisión

- Una versión de Redocly CLI con Redoc 3, o sin los errores de React.
- Si la documentación tiene que mostrar algo que el contrato no dice, como guías o ejemplos: entonces es un sitio, no
  una página.
- Si la API pasa a tener más de una versión publicada a la vez.
