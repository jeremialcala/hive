# Revisión dual del Gate 2

* **Estado:** approved (Gate 2, 2026-10-18)
* **Fecha:** 2026-10-18
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 03-implementation
* **Versión:** 0.3.0
* **Alcance:** todo lo que entró en el Gate 2, `v0.2.0..develop` (`d9a8247`): unas 3000 líneas de `src/`, la imagen,
  el compose, los workflows y una muestra de las pruebas

El Gate 2 pide una revisión dual, humana y de IA. Esta es la de la IA. La hicieron cuatro revisores en paralelo, uno
por área: autenticación y criptografía; datos, aislamiento y borrado; webhook y cola; imagen, CI y pruebas. Cada
hallazgo se comprobó leyendo el código, y la mayoría también reproduciéndolo con una prueba. Lo que no se pudo
sostener se descartó.

**Resultado:** ningún hallazgo crítico. Uno alto, siete medios y trece bajos. Trece están corregidos, dos de ellos en parte, en
el mismo pull request que este documento, cada uno con una prueba que falla con el código anterior y pasa con el nuevo.
Los demás quedan a tu decisión.

## Hallazgos

| # | Gravedad | Hallazgo | Estado |
|---|---|---|---|
| RD-01 | Alta | **Los gates no son obligatorios.** Ni `develop` ni `main` exigen que pasen las pruebas, gitleaks, `pip-audit` o CodeQL para fusionar. `develop` admite push directo y forzado, y la publicación de la imagen solo espera a las pruebas: un hallazgo alto de CodeQL o un secreto filtrado llegan igual a producción | **Decidido el 2026-10-18: asegurarlo.** Reglas preparadas para `main`, `develop` y las etiquetas `v*`: pull request obligatorio, las cinco comprobaciones (pruebas, imagen, gitleaks, `pip-audit`, CodeQL), sin push forzado ni borrado, y etiquetas de versión que no se mueven ni se borran. **Aplicadas el 2026-10-18** por Jeremi: `Protect-MAIN` (seis comprobaciones, con la de GitFlow), `Protect-DEVELOP` (cinco) y `Protect-TAGS` |
| RD-02 | Media | **Un cambio de claves simultáneo deshacía una revocación.** Añadir y quitar una clave leía la lista y la guardaba entera: si el operador revocaba una clave mientras otra petición añadía una, la revocada volvía a valer. La misma carrera recreaba un cliente ya borrado | **Corregido**: el alta y la baja son una sola actualización condicionada en la base |
| RD-03 | Media | **El perfil de Messenger se rellenaba con datos reales tuyos.** Lo que la Graph API no devolvía tomaba tu nombre, tu foto y tu identificador de Messenger, se guardaba como dato de esa persona y salía en el sobre de la cola y en su exportación. El identificador del contacto, además, salía de la respuesta de la Graph API y no del mensaje | **Corregido**: lo que falta queda vacío y el identificador sale del remitente. También se quitan tu identificador y tu nombre de otros DTO que no están en el camino del webhook |
| RD-04 | Media | **Una clave robada sobrevivía a la revocación documentada.** Con una clave de un cliente se puede dar de alta otra y quitar la legítima; el procedimiento decía que bastaba con revocar la comprometida, y el alta se escribía en el log a un nivel que producción no guarda | **Corregido** en parte: cada alta de clave deja un aviso con su `kid`, y cada baja otro, y el procedimiento de `autenticacion.md` pide quitar todas las que el cliente no reconozca. **Decidido el 2026-10-18: la firma de la clave nueva.** `POST /keys` exige una prueba que firma la clave que se añade, ligada al token de la misma petición. Impide registrar una clave con la que nadie puede firmar; no impide que quien robó una clave añada una suya. Contra eso queda la revocación por el operador |
| RD-05 | Media | **Las llamadas al servicio de claves y a MongoDB bloquean el proceso entero.** Se hacen sin soltar el bucle de eventos: si Andvari o la base tardan, se paran todas las peticiones, `/health` incluido, y el contenedor puede marcarse como enfermo | **Aceptado el 2026-10-18** hasta separar el webhook de la API de gestión, primer incremento del refactor y antes del primer despliegue: así una petición de gestión lenta deja de parar el webhook. Lo que queda dentro de cada proceso lo resuelven el worker de ingesta (I4) en el webhook y un incremento propio en la gestión. Anotado como H14. **La separación está hecha** ([ADR-0018](../00-project/adr/0018-webhook-y-gestion-por-separado.md)) |
| RD-06 | Media | **El análisis de dependencias no auditaba lo que instala la imagen.** Solo pasaba `pip-audit` por el lock de desarrollo, y nada obliga a que fije las mismas versiones que el de runtime | **Corregido**: se auditan los dos |
| RD-07 | Media | **Las pruebas de que el motor no deja tokens en el log solo veían los avisos.** Un token escrito en INFO o DEBUG, que es lo que guardan desarrollo y staging, pasaba | **Corregido**, con control negativo: un token escrito en INFO hace fallar la prueba |
| RD-08 | Media | **La migración de cifrado devolvía a la base un documento borrado mientras corría**, sin su cliente; la migración de asignación se lo daba después al operador | **Corregido**: guarda con la misma condición que el resto del repositorio |
| RD-09 | Baja | **Un POST sin firma llegaba al servicio de claves** antes de comprobar la cabecera: respondía `503` en vez de `403` con Andvari caído, y cualquiera que conociera la dirección de un bot hacía al gateway abrir la clave de su cliente | **Corregido**: sin nada que tenga forma de firma, `403` sin abrir nada |
| RD-10 | Baja | **Repetir las supresiones desde un directorio que no existe decía «nada que hacer»**, y lo borrado volvía a estar en servicio tras restaurar | **Corregido**: un registro que no está es un error, y el comando lo dice |
| RD-11 | Baja | **Un intent borrado se podía leer y modificar**, aunque ya no se listaba | **Corregido**: responde `404`. La prueba anterior conservaba ese comportamiento a propósito y se sustituye |
| RD-12 | Baja | **El emisor de un token se leía de bytes que la firma no cubre**, porque se aceptaba la forma JSON del JWS. No da más acceso, porque la firma tiene que ser de esa misma clave, pero contradecía la regla del propio módulo | **Corregido**: solo la forma compacta, y el emisor firmado tiene que ser el mismo |
| RD-13 | Baja | **Una respuesta malformada del servicio de claves acababa en `500`** en vez de `503` | **Corregido** |
| RD-14 | Baja | **El cifrado de un campo no está ligado a su documento.** Quien pueda escribir en la base puede cambiar un valor cifrado por otro del mismo cliente (el App Secret de un bot por el de otro) y se descifra bien | **Aceptado el 2026-10-18** hasta que cambie el formato del cifrado: exige acceso de escritura a Atlas |
| RD-15 | Baja | **Las acciones de GitHub van por etiqueta y no por commit**, también la que publica la imagen; gitleaks se descarga sin comprobar su suma, y `pip-audit` y `uv` sin versión fija | **Pendiente**: propongo fijarlas en un PR aparte |
| RD-16 | Baja | **Valores por defecto que alcanzan a producción.** `/docs`, `/redoc` y `/openapi.json` están abiertos en todos los entornos, y `ENVIRONMENT` admite cualquier texto: con una errata, producción escribe a nivel INFO y se salta la comprobación de HTTPS de Andvari | **Corregido**: el gateway no está desplegado en midgard (comprobado el 2026-10-18), así que no había valor que respetar. `ENVIRONMENT` solo admite los tres valores y con otro no arranca; la documentación interactiva solo se sirve en `development`; el compose de despliegue lo fija en `production` |
| RD-17 | Baja | **Endurecimiento del despliegue.** El `.dockerignore` solo excluía secretos en la raíz; el logger de staging escribe un fichero donde la aplicación no tiene permiso; el compose no lleva `read_only`, `cap_drop` ni `no-new-privileges` | **Corregido** el `.dockerignore`. **Pendiente** lo demás, para el Gate 4 |
| RD-18 | Baja | **Una prueba de autenticación fallaba si la batería tardaba más de 90 s** en llegar a ella | **Corregido** |
| RD-19 | Baja | **Un rechazo permanente del servicio de claves responde `503` siempre**, y Messenger da de baja la página a la hora | **Aceptado el 2026-10-18**: se arregla en Andvari, y el log ya distingue «no responde» de «rechaza» con su código |
| RD-20 | Baja | **Añadir reglas no tiene límite**: tres consultas por regla, y en 3 MiB caben cientos de miles | Ya en H6 |
| RD-21 | Baja | **El JSON del sobre depende del formato heredado de mongoengine**, que avisa de que lo va a quitar | **Pendiente**: la prueba de contrato lo detectaría; conviene fijarlo cuando se escriba el contrato v2 |

## Lo que los revisores comprobaron y encontraron bien

- **Tokens:** un solo algoritmo por capa, vida de 60 s como mucho, un solo uso con el `jti` como clave primaria, y
  el mismo `401` para cualquier rechazo. El papel lo decide el registro, no el token. El motor solo alcanza sus
  cuatro rutas, y una ruta nueva nace cerrada para él.
- **Aislamiento:** toda consulta pasa por el cliente; lo ajeno responde igual que lo inexistente; la búsqueda y la
  exportación no cruzan clientes.
- **Cifrado:** nonce aleatorio por valor, clave de índice ciego separada de la de cifrado, clave de datos derivada
  por cliente en Andvari, y la carrera al crear la primera clave de un cliente resuelta.
- **Borrado:** el registro se escribe antes de borrar, nunca para lo ajeno; la repetición es idempotente y cubre
  14 meses; un mensaje en camino no deja contactos huérfanos.
- **Webhook:** la firma se calcula sobre los bytes crudos y se compara en tiempo constante; el cuerpo no se lee
  antes; el límite de 3 MiB se aplica también a un cuerpo sin longitud declarada; `503` solo por infraestructura.
- **Logs:** ninguna línea lleva lo que manda quien llama; el log de accesos está apagado.
- **Imagen y CI:** imagen base fijada por digest, dependencias con hashes, usuario sin privilegios, puerto solo en
  local, permisos mínimos en los workflows y sin inyección de datos del evento.

## Cómo se verificaron las correcciones

- Cada corrección trae su prueba, y cada prueba se pasó también con el código anterior: falla.
- La batería entera pasa en local (946 pruebas) y contra un MongoDB 8.0 real y desechable, porque las altas y bajas
  de claves usan operadores de MongoDB que la base en memoria solo imita.
- Las pruebas contra OpenBao y RabbitMQ reales corren en el pipeline.

## La parte humana

La revisión dual pide además la tuya. Decisiones del 2026-10-18: asegurar el repositorio (RD-01, ya aplicado), la firma de la
clave nueva para añadirla (RD-04) y aceptar RD-14 y RD-19. RD-16 se corrigió sin esperar el valor de midgard: el gateway no está desplegado allí.

El mismo día aceptó RD-05, hasta separar el webhook de la API de gestión, y aprobó el cierre del Gate 2. Con eso se
corta la 0.3.0.

Aparte: tu nombre, tu identificador de Messenger y la URL de tu foto de perfil siguen en la historia del
repositorio, que es público. Es el mismo caso que los números reales de commits anteriores, todavía pendiente de
decisión.
