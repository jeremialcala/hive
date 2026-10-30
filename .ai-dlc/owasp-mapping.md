# Matriz OWASP Top 10:2025 → controles de Hive

* **Estado:** review
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 04-testing
* **Versión:** 1.3.0

Lo que Hive hace de verdad contra cada riesgo del OWASP Top 10:2025, con lo que lo prueba y dónde se decidió. Sustituye
a la copia de la plantilla, que estaba «sin adaptar todavía» (HIG-133, Gate 3).

Cómo se lee:
- **Las rutas son del repositorio.** `tu/` es `tests/unit/`, y `ptu/` es `portal/tests/unit/`.
- **RSxx** es la fila de los requisitos de seguridad del [PRD](../docs/01-requirements/mvp-baas-gateway.md), que ya
  trae su columna OWASP.
- **Cada hueco lleva su tarea.** Lo que el PRD da por pendiente se cita por su RS.

Todas las pruebas citadas existen y corren en el CI: `gateway · test`, `engine · test` y `portal · test`.
- **La suite de seguridad del gateway** se corre sola con `pytest -m security`: son `tests/security/` y las
  pruebas unitarias de esta matriz, que `tests/conftest.py` marca.
- **`tests/e2e/`** recorre del webhook a la respuesta con las imágenes de producción ([e2e](../docs/04-testing/e2e.md)).

## Resumen

| ID | Riesgo | Estado | Lo que más lo sostiene | Lo que falta |
|---|---|---|---|---|
| A01 | Control de acceso | Cubierto en la API | Aislamiento por cliente y matriz rol × acción, probados ruta por ruta | DAST con una sesión de cada rol: `dast · zap` va sin token; RS20, RS24 |
| A02 | Configuración insegura | Parcial | Contenedores sin root, endurecidos; documentación solo en desarrollo; cabeceras en el portal, `hive-api` y el webhook | Escaneo de imagen y de compose (HIG-20) |
| A03 | Cadena de suministro | Parcial | Locks con hashes, imágenes por digest, `pip-audit`, gitleaks, CodeQL con umbral | SBOM, Actions por SHA, Dependabot (HIG-20) |
| A04 | Fallos criptográficos | Cubierto, con una salvedad | Clave de datos por cliente en Andvari, AES-GCM con el cliente autenticado, sesiones selladas | Cifrado de lo que viaja por el bus (RS08); RS28 |
| A05 | Inyección | Parcial | Solo ODM por el repositorio; ids, operadores y anidamiento hostiles en cada ruta; 422 sin eco | Fuzzing de los cuerpos con token: `dast · zap` no pasa del `401`; RS11 |
| A06 | Diseño inseguro | Parcial | Threat model, límites de `/oauth/token` y de envíos, `Idempotency-Key`, ventana y consentimiento | Límites en el resto de rutas (HIG-139) |
| A07 | Identificación y autenticación | Cubierto | Aserción firmada y cifrada de un solo uso, DPoP, MFA en el `amr`, BFF con PKCE | DAST con la sesión abierta: `dast · zap` escanea el login sin ella |
| A08 | Integridad de software y datos | Parcial | Firma de Meta sobre el cuerpo crudo, contratos versionados, imágenes solo tras las pruebas | Firma de imágenes y procedencia (HIG-20) |
| A09 | Registro y monitorización | Parcial | Logs sin datos personales, `WARNING` de auditoría, trazas sin PII, vigía | Alertas, tablero y runbook (HIG-140); RS14, RS19, RS25 |
| A10 | Condiciones excepcionales | Cubierto | 503 para que Meta reintente, fallo cerrado, 500 genérico, bus que reentrega | Mutation testing (HIG-137); RD-19 aceptado |

## A01 — Control de acceso

- **Aislamiento por cliente en la capa de datos.** `TenantRepository` (`src/repository/tenant.py`) filtra todo por
  cliente, y lo de otro cliente responde `404`, igual que lo que no existe.
  - **Prueba:** `tu/test_client_isolation.py` (`test_every_route_of_a_chatbot_is_covered`,
    `test_another_client_gets_not_found`) y `tu/test_tenant_repository.py`.
  - **Decidido en:** ADR-0012; RS01.
- **Nada consulta la base fuera del repositorio.**
  - **Prueba:** `tu/test_data_access_boundary.py::test_only_the_data_layer_touches_the_database`, que lee el código.
- **Matriz rol × acción, denegada por defecto.** `ANALYST_ROUTES`, `WORKER_ROUTES` y `SERVICE_ROUTES` en `src/app.py`,
  aplicadas en `get_principal`. Lo que no está listado da `403`.
  - **Prueba:** `tu/test_role_matrix.py` (`test_every_route_that_asks_for_a_token_is_in_the_matrix_and_nothing_else`,
    `test_each_role_reaches_in_the_code_what_the_matrix_says`).
  - **Decidido en:** `docs/02-design/matriz-rol-accion.md`; ADR-0022, ADR-0029.
- **Una credencial por worker del motor,** que solo llega a sus rutas.
  - **Prueba:** `tu/test_service_credential.py`, `tu/test_engine_keys.py`.
  - **Decidido en:** ADR-0016, ADR-0022; RS21.
- **El `bot_id` identifica, no autoriza.** Solo las rutas del webhook funcionan con él solo, y las autentica la firma
  de Meta.
  - **Prueba:** `tu/test_two_services.py::test_the_webhook_serves_what_meta_calls_and_nothing_else`.
  - **Decidido en:** ADR-0018; RS18.
- **El cliente y el rol de una persona salen de Hive (`ClientUser`), no del IdP**
  (`src/auth/principal.py`, `exchange_id_token`).
  - **Prueba:** `tu/test_people.py::test_a_person_blocked_or_removed_gets_no_new_token`.
  - **Decidido en:** ADR-0029; RS23.
- **Los destinos de los intents, solo HTTPS público** (anti-SSRF): `shared/destinations.py`.
  - **Prueba:** `tu/test_intent_destinations.py::test_a_destination_that_could_reach_the_platform_is_refused`.
  - **Decidido en:** `docs/03-implementation/destinos-de-los-intents.md`; RS16.

**Huecos:**
- el DAST ([`dast · zap`](../docs/04-testing/dast.md), HIG-90) va sin token: no prueba cuentas de distinto rol, solo que
  cada ruta con token responde `401` sin él
  (`tests/e2e/test_dast.py::test_every_route_that_asks_for_a_token_answers_401_without_one`);
- RS20, credenciales por worker en el bus del gateway, sigue pendiente en el PRD;
- RS24, acciones destructivas del operador con MFA y motivo, también; es el backoffice de HIG-100.

## A02 — Configuración insegura

- **La documentación interactiva y el OpenAPI, solo en desarrollo** (`src/service.py`; el portal, con `docs_url=None`).
  Un entorno desconocido no arranca.
  - **Prueba:** `tu/test_logs_and_errors.py::test_the_documentation_is_only_served_in_development`,
    `test_an_environment_that_does_not_exist_stops_the_start`.
- **TLS exigido fuera de desarrollo** para Andvari (`https://`) y Bifröst (`tls://`).
  - **Decidido en:** ADR-0013, ADR-0019.
- **El webhook y la gestión son dos servicios,** y el túnel publica cada uno por su nombre.
  - **Prueba:** `tu/test_two_services.py`.
  - **Decidido en:** ADR-0018, ADR-0021, ADR-0024; RS09, RS13.
- **Contenedores sin root (uid 10001)** en los tres Dockerfile. Los compose llevan `read_only`, `no-new-privileges` y
  `cap_drop: [ALL]`.
  - **Prueba:** `tu/test_idp_compose.py::test_every_container_drops_its_capabilities_and_cannot_gain_privileges`, solo
    para el compose de Zitadel.
  - **Decidido en:** RS17.
- **Cabeceras del navegador en el portal** (`portal/src/app.py`): CSP en `'self'`, HSTS, `nosniff`, `X-Frame-Options`,
  `Referrer-Policy`, y `no-cache` en los ficheros de la PWA.
  - **Prueba:** `ptu/test_portal.py::test_every_page_says_what_the_browser_may_load`,
    `test_the_pwa_s_files_are_asked_again_after_a_deploy`.
  - **Decidido en:** ADR-0026, ADR-0027, ADR-0034.
- **Las cabeceras de una API JSON en `hive-api` y el webhook** (`SECURITY_HEADERS` en `src/service.py`, HIG-138), en
  toda respuesta, también en un `404`, un `413` o un `500`:
  - HSTS, `nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` y `Cache-Control: no-store`;
  - fuera de desarrollo, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`.
  - **Prueba:** `tests/security/test_security_headers.py` (`test_every_answer_carries_the_headers_of_a_json_api`,
    `test_a_failure_carries_them_too`).
  - **Decidido en:** OWASP REST Security Cheat Sheet.
- **Sin secretos en el repositorio ni en las imágenes** (`.gitignore`, `.dockerignore`).
  - **Prueba:** gitleaks («Fuga de secretos», `seguridad.yml`), sobre toda la historia.
  - **Decidido en:** `SECURITY.md`; RS13.
- **Protección de las ramas:** `GitFlow guard` y los rulesets `Protect-MAIN` y `Protect-DEVELOP`, que exigen los checks
  del CI. Los rulesets viven en GitHub, no en el repositorio.

**Huecos:**
- **No hay escaneo de imagen** (Trivy) **ni de compose o Dockerfile** (hadolint), que son gates de Gate 4 (HIG-20).

## A03 — Cadena de suministro

- **Dependencias fijadas con hashes** (`requirements*.lock` en la raíz, `engine/` y `portal/`), instaladas con
  `--require-hashes`, y en el motor y el portal solo ruedas (`--only-binary=:all:`).
  - **Decidido en:** `SECURITY.md`; RS12.
- **SCA:** «SCA · dependencias vulnerables» (`seguridad.yml`) pasa `pip-audit --strict` por los seis locks en cada
  push, en cada PR y cada semana. Comprueba además que cada lock sale de su `.txt`.
- **Imágenes base por digest** (`@sha256:`) en los cuatro Dockerfile.
  - **Prueba:** `tu/test_idp_compose.py::test_every_image_is_pinned_by_digest`, para el compose de Zitadel.
  - **Decidido en:** threat model, T14; RS12.
- **Secretos:** gitleaks 8.28.0, con `--redact`, sobre toda la historia de todas las ramas.
- **SAST:** «SAST · CodeQL», con las consultas `security-and-quality`. `.ai-dlc/scripts/sast_threshold.py` rompe el
  build con un hallazgo de 7.0 o más fuera de las pruebas. `main` tiene 0 alertas abiertas desde la 0.4.0.
  - **Prueba:** `tu/test_sast_threshold.py::test_a_high_security_finding_breaks_the_build`.

**Huecos** (todos de HIG-20, Gate 4):
- no se genera SBOM (RS12, «Parcial»);
- no hay Dependabot ni Renovate;
- no hay gate de licencias;
- las Actions van por etiqueta (`@v4`), no por SHA.

## A04 — Fallos criptográficos

- **Una clave de datos por cliente,** envuelta por Andvari (transit, AppRole). Los campos van con AES-GCM y llevan el
  cliente en lo autenticado. El índice ciego es HMAC-SHA256 con una clave derivada por HKDF (`src/crypto/fields.py`,
  `src/crypto/key_service.py`).
  - **Prueba:** `tu/test_client_encryption.py` (`test_a_value_does_not_open_in_another_field_nor_for_another_client`,
    `test_a_copy_of_the_database_shows_no_number_and_no_credential`) y
    `tests/integration/test_openbao_key_service.py::test_a_wrapped_key_does_not_open_for_another_client`.
  - **Decidido en:** ADR-0010, ADR-0013; `docs/03-implementation/cifrado-de-datos.md`; RS05, RS06.
- **Los tokens de Meta y el App Secret, cifrados.** No vuelven por la API de gestión, y el motor solo recibe los que
  usa.
  - **Prueba:** `tu/test_client_encryption.py::test_the_answer_to_loading_credentials_does_not_repeat_them`,
    `tu/test_service_credential.py`.
  - **Decidido en:** ADR-0007; RS06.
- **Algoritmos fijos:** la aserción es un JWT ES256 dentro de un JWE (RSA-OAEP-256 con A256CBC-HS512), en
  `shared/auth/tokens.py`.
  - **Prueba:** `tu/test_authentication.py::test_another_signature_algorithm_is_rejected`.
  - **Decidido en:** ADR-0005, ADR-0009, ADR-0025.
- **Sesiones del portal selladas con AES-GCM,** guardadas bajo el SHA-256 de su id (`portal/src/sessions.py`).
  - **Prueba:** `ptu/test_sessions.py::test_a_value_tampered_with_or_moved_to_another_name_opens_to_nothing`.
  - **Decidido en:** ADR-0034.
- **Comparación en tiempo constante** del verify token y de la firma (`src/utils/security.py`, `hmac.compare_digest`).
  - **Decidido en:** RS04.

**Huecos:**
- lo que viaja por el bus no va cifrado: el JWE existe pero está desactivado (RS08, «Parcial»);
- las credenciales de las integraciones siguen pendientes (RS28; HIG-102);
- RD-14 está aceptado: un valor cifrado puede moverse a otro documento del mismo cliente.

## A05 — Inyección

- **La base solo se toca por el ODM** (MongoEngine) y por el repositorio. Los dos `__raw__` de
  `src/repository/clients.py` arman sus condiciones en el servidor.
- **Validación estricta, y un `422` que no repite la entrada** (`invalid_request` en `src/service.py`).
  - **Prueba:** `tu/test_export_and_delete.py::test_a_search_that_is_not_well_formed_is_rejected_without_repeating_it`.
  - **Decidido en:** RS11 («Parcial»).
- **Nada se inyecta en el log:** una petición queda en una línea.
  - **Prueba:** `tu/test_logs_and_errors.py::test_an_address_cannot_plant_anything_in_the_log`,
    `tu/test_outbound.py::test_what_goes_into_the_log_from_a_request_stays_on_one_line`.
- **El cuerpo de Meta se lee solo después de la firma,** y no se rellena lo que falta.
  - **Prueba:** `tu/test_webhook_answers.py::test_nothing_is_filled_in_for_what_a_notification_lacks`.
- **Lo que dice el LLM no es de fiar:** el clasificador falla cerrado.
  - **Prueba:** `engine/tests/unit/test_clasificador_llm.py::test_una_respuesta_que_no_valida_no_es_un_veredicto`.
  - **Decidido en:** ADR-0004 y ADR-0005 del motor; RS16, RS26.
- **Lo hostil, en cada ruta** (`tests/security/test_hostile_input.py`, HIG-134).
  - **Qué cubre:** un id hostil en cada parámetro de la dirección de cada ruta del cliente, uno por llamada, con
    inyección SQL y NoSQL, rutas relativas, `NUL`, HTML y 5 000 caracteres. Operadores de MongoDB donde se espera
    texto, una `Idempotency-Key` desmedida y una notificación firmada con 200 000 niveles de anidamiento.
  - **Qué exige:** siempre un 4xx, nada que coincida y nada repetido en la respuesta. Lo primero que encontró fueron
    trece `404` y `400` que devolvían el id o el nombre recibidos, ya corregidos.
  - **Prueba:** `tests/security/test_hostile_input.py` (`test_a_hostile_id_in_the_address_is_a_4xx_that_matches_nothing`,
    `test_a_mongodb_operator_where_text_is_expected_is_refused`).
- **Los destinos de los intents:** ver A01.

**Huecos:**
- no hay fuzzing de los cuerpos de las rutas con token: el DAST ([`dast · zap`](../docs/04-testing/dast.md), HIG-90)
  solo llega hasta su `401`;
- los modelos de gestión no acotan todos sus campos (RS11);
- el catálogo de herramientas MCP sigue pendiente (RS29).

## A06 — Diseño inseguro

- **Threat model** con DFD, STRIDE y DREAD (`docs/02-design/threat-model.md`), y escenarios de abuso en el PRD.
- **Límites de `/oauth/token`:**
  - **Qué limita:** por origen, por fallos y por clave, con el IPv6 contado por /64 (`src/auth/token_limits.py`). En el
    borde hay además un límite en Cloudflare.
  - **Prueba:** `tu/test_token_limits.py::test_an_origin_that_keeps_failing_is_turned_away_for_ten_minutes`.
  - **Decidido en:** ADR-0028; RS10.
- **Límites de envío:**
  - **Qué limita:** 60 por minuto por bot y 1 000 al día por cliente, con los contadores en Bifröst
    (`src/controller/send_limits.py`). La `Idempotency-Key` es obligatoria.
  - **Prueba:** `tu/test_outbound.py::test_the_same_idempotency_key_sends_once`,
    `tests/integration/test_send_limits_bus.py`.
  - **Decidido en:** ADR-0031; RS27.
- **Ningún cuerpo de más de 3 MiB se lee** (`src/utils/body_limit.py`).
  - **Prueba:** `tu/test_authentication.py::test_an_oversized_request_is_not_parsed`,
    `ptu/test_portal.py::test_a_body_too_big_is_not_sent`.
- **La ventana de 24 horas y el consentimiento** en lo que envía el cliente.
  - **Prueba:** `tu/test_outbound.py::test_a_text_outside_the_window_is_turned_away`.
  - **Decidido en:** ADR-0031.
- **El estado del bot lo mueve solo lo que dice Meta:** una firma mala no lo pone en ERR (`src/controller/bot_state.py`).
  - **Prueba:** `tu/test_bot_state.py`.
- **Exportar y borrar (RGPD),** con un registro de supresiones que no guarda lo borrado.
  - **Prueba:** `tu/test_export_and_delete.py`, `tu/test_erasure_register.py`.
  - **Decidido en:** ADR-0014; `docs/03-implementation/exportar-y-borrar.md`.

**Huecos:** el resto de rutas de gestión y el webhook no tienen límite en la aplicación (RS10, «Parcial»; HIG-139).

## A07 — Identificación y autenticación

- **Credencial de máquina:**
  - **Qué es:** OAuth2 `client_credentials` con `private_key_jwt`. La aserción es un JWT dentro de un JWE, vale 60 s y
    su `jti` es de un solo uso (`shared/auth/tokens.py`, `src/auth/principal.py`).
  - **Prueba:** `tu/test_authentication.py` (`test_an_assertion_is_good_once`,
    `test_a_revoked_key_gets_no_new_token_at_once`).
  - **Decidido en:** ADR-0005, ADR-0009, ADR-0025; `docs/03-implementation/autenticacion.md`; RS02.
- **DPoP (RFC 9449) en todo token de acceso:**
  - **Qué es:** una prueba de un solo uso por método y URL, de 60 s y con `ath` (`src/auth/dpop.py`).
  - **Prueba:** `tu/test_dpop.py` (`test_a_token_without_its_proof_is_turned_away`,
    `test_a_stolen_token_is_good_for_nothing_with_a_proof_of_another_key`).
  - **Decidido en:** ADR-0033; RS30.
- **Prueba de posesión antes de añadir una clave** (`shared/auth/key_proof.py`).
  - **Prueba:** `tu/test_key_proof.py::test_a_proof_does_not_serve_twice`.
- **Las personas entran con Zitadel y el canje de tokens.** Se exige `mfa` en el `amr`, y Zitadel lo manda: se comprobó
  en el primer inicio de sesión real, el 2026-10-30.
  - **Prueba:** `tu/test_people.py::test_without_the_second_factor_in_the_amr_there_is_no_token`.
  - **Decidido en:** ADR-0029; RS22.
- **El BFF del portal:**
  - **El inicio de sesión:** PKCE S256, `state` y `nonce`.
  - **Las cookies:** `__Host-` con `HttpOnly`, `Secure` y `SameSite=Lax`.
  - **El CSRF:** la cabecera `X-Hive-Portal`.
  - **La sesión:** caduca a los 30 minutos sin uso y a las 12 horas en total.
  - **Prueba:** `ptu/test_portal.py` (`test_the_login_goes_to_zitadel_with_pkce_state_and_nonce`, `test_a_state_is_good_once`,
    `test_a_stolen_cookie_without_the_pwa_s_header_changes_nothing`).
  - **Decidido en:** ADR-0029, ADR-0034.
- **El verify token del webhook:** un bot sin él no se verifica nunca.
  - **Prueba:** `tu/test_webhook_security.py::test_verification_never_passes_for_a_bot_without_verify_token`.
  - **Decidido en:** RS04.

**Huecos:** el DAST ([`dast · zap`](../docs/04-testing/dast.md), HIG-90) recorre el login, su callback, la sesión y el
logout sin una sesión abierta; con ella, no.

## A08 — Integridad de software y datos

- **La firma de Meta** (`X-Hub-Signature-256`), un HMAC sobre el cuerpo crudo con el App Secret de cada bot
  (`src/utils/security.py`, `src/webhook.py`).
  - **Prueba:** `tu/test_webhook_security.py::test_signature_matches_only_the_signed_body`,
    `tu/test_webhook_answers.py::test_without_a_good_signature_the_answer_is_403_and_the_body_is_not_read`, y con las
    imágenes de producción,
    `tests/e2e/test_del_webhook_a_la_respuesta.py::test_a_notification_signed_with_another_secret_reaches_nothing`.
  - **Decidido en:** ADR-0004, ADR-0007; RS03.
- **Contratos versionados** del bus, de la cola y de la API.
  - **Prueba:** `tests/contract/` (`test_bus_contract.py`, `test_queue_contract.py`, `test_openapi_contract.py`) y
    `engine/tests/contract/`.
  - **Decidido en:** ADR-0011, ADR-0019.
- **Un `messageId` determinista,** para que Bifröst descarte lo repetido.
  - **Prueba:** `tu/test_webhook_event.py::test_the_same_notification_sent_again_has_the_same_message_id`.
- **Solo se publica una imagen que pasó las pruebas** (`needs: test` en `build.yml`), y producción se construye desde
  la etiqueta de una release ([releases](../docs/05-deployment/releases.md)).

**Huecos** (de HIG-20): no hay firma de imágenes (cosign), ni atestación de procedencia, ni etiquetas de release
firmadas.

## A09 — Registro y monitorización

- **Los logs no llevan datos personales ni secretos:** un fallo se describe por su tipo y su línea, y el log de accesos
  está apagado en los tres servicios.
  - **Prueba:** `tu/test_logs_and_errors.py::test_nothing_a_caller_or_meta_sends_reaches_the_log` y
    `engine/tests/unit/test_worker_clasificador.py::test_el_log_no_lleva_el_texto_ni_el_numero_ni_el_id_del_mensaje`.
  - **Decidido en:** RS07.
- **`WARNING` de auditoría,** el nivel que producción guarda:
  - los rechazos;
  - los tokens entregados al motor;
  - cada borrado;
  - cada envío y cada consentimiento;
  - cada lectura de una conversación por una persona;
  - cada cambio de estado de un bot.
  - **Prueba:** `tu/test_outbound.py::test_the_log_says_who_sent_what_to_whom_and_never_the_text_nor_the_number`,
    `tu/test_export_and_delete.py::test_every_deletion_leaves_a_trace_at_the_level_production_logs_at`.
  - **Decidido en:** RS14 («Parcial»).
- **Trazas sin datos personales,** en Saga.
  - **Prueba:** `tu/test_traces.py::test_the_span_of_a_request_names_its_route_never_its_address`.
  - **Decidido en:** `docs/03-implementation/trazas.md`.
- **El vigía** (`deploy/vigia/hive-vigia.sh`): reinicia lo que no está healthy y avisa por ntfy.
  - **Prueba:** `tu/test_hive_vigia.py::test_reinicia_lo_unhealthy_y_avisa`.

**Huecos:**
- `docs/06-monitoring/` está vacío: no hay alertas de seguridad, ni tablero, ni runbook de incidentes (HIG-140);
- la auditoría por cliente, legible por el `cliente-admin`, es HIG-95 (RS14, RS25);
- los eventos del bus sin datos personales siguen pendientes (RS19).

## A10 — Condiciones excepcionales

- **Sin una dependencia, el webhook responde `503` para que Meta reintente:** el bus, la base o Andvari
  (`RETRY_LATER` en `src/controller/ingestion.py`).
  - **Prueba:** `tu/test_webhook_event.py::test_an_event_the_bus_does_not_keep_is_a_503`,
    `tu/test_webhook_security.py::test_processing_does_not_swallow_a_broker_failure`.
  - **Decidido en:** ADR-0006, ADR-0015, ADR-0021.
- **Sin Andvari, nada cifrado se da por bueno:** el webhook no se verifica y pide a Meta que reenvíe.
  - **Prueba:** `tu/test_client_encryption.py::test_without_the_key_service_the_webhook_is_not_verified`.
  - **Decidido en:** `docs/03-implementation/cifrado-de-datos.md`.
- **Qué cuenta como fallo de la base:** `database_failed`, que no incluye las claves duplicadas.
  - **Prueba:** `tu/test_webhook_answers.py::test_what_counts_as_a_failure_of_the_database`,
    `tu/test_database_connection.py::test_every_wait_of_the_connection_has_an_end`.
- **Un `500` genérico, sin detalles internos.**
  - **Prueba:** `tu/test_logs_and_errors.py::test_the_text_of_a_failure_is_neither_answered_nor_logged`.
  - **Decidido en:** RS15.
- **Fallo cerrado:**
  - sin veredicto, el clasificador bloquea;
  - todo rechazo de token es el mismo `401`;
  - un IdP que no responde es «inténtalo más tarde».
  - **Prueba:** `tu/test_people.py::test_when_the_keys_of_the_idp_cannot_be_read_it_is_for_later`,
    `ptu/test_portal.py::test_when_zitadel_cannot_be_reached_it_is_for_later`.
- **Lo que el bus no acusa queda para reentregarse.**
  - **Prueba:** `tu/test_ingestion.py::test_an_event_that_has_to_wait_is_left_for_bifrost_to_deliver_again`.

**Huecos:**
- no hay mutation testing que diga si estas pruebas cazan un fallo de verdad (HIG-137);
- RD-19 está aceptado: un rechazo permanente de Andvari responde siempre `503`.
