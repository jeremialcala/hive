# Mutation testing de los módulos de seguridad

* **Estado:** review
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 04-testing
* **Versión:** 1.0.0

Si las pruebas de los módulos donde un fallo es un fallo de seguridad cazan un fallo de verdad (HIG-137, Gate 3). La
cobertura dice qué líneas corren; esto dice si alguna prueba se entera cuando una de ellas cambia.

## Qué se muta

`setup.cfg`, sección `[mutmut]`:

| Módulo | Qué guarda |
|---|---|
| `src/auth/access_tokens.py` | Los tokens de acceso que firma el gateway (ADR-0025) |
| `src/auth/dpop.py` | Las pruebas DPoP de cada petición (ADR-0033) |
| `src/auth/principal.py` | Quién llama: la aserción, el token y el ID token de una persona |
| `src/auth/token_limits.py` | Los límites de `POST /oauth/token` (ADR-0028) |
| `src/auth/client_keys.py` | La clave pública que registra un cliente |
| `src/auth/request_limits.py` | Los límites del resto de rutas y del webhook (HIG-139) |
| `src/utils/security.py` | La firma de Meta en el webhook |
| `src/controller/bot_state.py` | Qué mueve el estado de un chatbot (HIG-74, HIG-75) |
| `src/crypto/fields.py` | El cifrado por cliente y el índice ciego |

Los juzgan `tests/unit` y `tests/security`. Las de integración no: sin sus servicios se saltan, y una prueba saltada
no mata nada.

## La herramienta: mutmut

**mutmut 3.8.0**, fijado en `requirements-mutation.lock`.
- **Es de pytest:** corre la suite tal como está, con su `conftest.py`.
- **Elige las pruebas de cada mutante:** una pasada con cobertura apunta qué pruebas llegan a cada función, y cada
  mutante corre solo esas, de la más rápida a la más lenta, hasta la primera que falla.
- **Se configura en un fichero** y no pide tocar el código.

Las alternativas:
- **cosmic-ray** pide una sesión en una base y un operador por línea de comandos, para lo mismo.
- **mutatest** lleva años sin publicar versiones.

**Solo corre en Linux:** hace un `fork` por mutante. En Windows se corre en Docker, como abajo.

## Cómo corre

En el workflow `mutation`, que **informa y no bloquea**. Corre:
- **cada lunes,** sobre `main`;
- **a mano,** sobre la rama que se pida;
- **en un pull request** que toque uno de esos módulos o esta configuración.

No es un check obligatorio, y solo falla si mutmut no puede correr. El 60 % del Gate 3 es un objetivo: por debajo deja
un aviso en el resumen del job, con la tabla por módulo y los mutantes que sobreviven. Bloquear desde el primer día con
un número que nadie ha mirado haría que se baje el número, no que suban las pruebas.

**Tarda más de una hora.** Cada mutante de la autenticación que sobrevive corre las 1 600 pruebas que pasan por ella.
La medición de abajo tardó unos 80 minutos con 8 procesos. El runner de GitHub tiene 4, así que el job tiene 300
minutos.

En local, desde la raíz:

```sh
docker run --rm -v "$PWD:/src:ro" python:3.12-slim sh -c '
  cp -r /src /work && cd /work &&
  pip install -q --require-hashes -r requirements-mutation.lock &&
  mutmut run && python .ai-dlc/scripts/mutation_score.py mutants && mutmut results'
```

- **`mutmut show <mutante>`** enseña el cambio de uno.
- **`mutmut run <mutante>`** vuelve a correr solo ese, por ejemplo tras añadir una prueba.

La puntuación por módulo la da `.ai-dlc/scripts/mutation_score.py` (lo prueba `tests/unit/test_mutation_score.py`):
detectados (muertos o en timeout) entre detectados, supervivientes y sin pruebas.

## Lo que hubo que arreglar para que midiera

Cuatro trampas. Con cualquiera de ellas la puntuación salía, y era falsa:
- **Pruebas con nombre al azar.** Seis pruebas se parametrizaban con `str(uuid4())` y su id cambiaba en cada
  recogida. mutmut apunta las pruebas por su id, y al volver a pedirlas pytest no las encontraba.
  - **Ahora:** llevan `ids=`.
- **Ids que pytest no sabe leer.** Tres casos de `test_token_limits.py` llevaban una IPv6 en el id, con su `::`.
  - **Ahora:** también llevan `ids=`.
- **Una sesión de pytest detrás de otra en el mismo proceso.** Con el aislamiento por defecto, el proceso que reparte
  los mutantes ya ha corrido la suite entera, y la segunda pasada encuentra el fichero de claves del gateway cambiado.
  Con el servidor de forks precalentado, que solo recoge las pruebas, los fixtures salen rotos. Con cualquiera de los
  dos **todo mutante moría**, de eso y no de las pruebas.
  - **Ahora:** `process_isolation = forkserver` y `forkserver_warmup = none`.
- **El 255.** mutmut cuenta como timeout el código 255, que con el servidor de forks es el `-1` de un worker al que pytest
  ni siquiera arrancó. Contado como detectado, la primera medición daba más del 90 %.
  - **Ahora:** el script lo cuenta aparte, sin veredicto, y avisa.

Dos pruebas de `test_two_services.py` arrancan un servicio en un proceso propio, que hereda la variable con la que
mutmut distingue sus mutantes y no encuentra su configuración. Prueban cómo arranca cada servicio, no estos módulos:
mutmut las deja fuera.

## La puntuación

Medida el 2026-10-30 en local, en Docker (`python:3.12-slim`, 8 procesos), sobre `develop` en `b0887a7`. Primero con
las pruebas que había, y después con las que añadió esta tarea.

| Módulo | Mutantes | Antes | Después | Sobreviven después |
|---|---:|---:|---:|---:|
| `src/auth/access_tokens.py` | 273 | 83,9 % (229) | 98,9 % (270) | 3 |
| `src/auth/client_keys.py` | 83 | 73,5 % (61) | 100 % (83) | 0 |
| `src/auth/dpop.py` | 186 | 83,3 % (155) | 98,4 % (183) | 3 |
| `src/auth/principal.py` | 178 | 86,0 % (153) | 98,3 % (175) | 3 |
| `src/auth/token_limits.py` | 107 | 92,8 % (90) | 97,9 % (95) | 2 |
| `src/controller/bot_state.py` | 46 | 89,1 % (41) | 100 % (46) | 0 |
| `src/crypto/fields.py` | 69 | 81,2 % (56) | 98,6 % (68) | 1 |
| `src/utils/security.py` | 35 | 100 % (35) | 100 % (35) | 0 |
| **Total** | **977** | **84,8 % (820)** | **98,8 % (955)** | **12** |

Entre paréntesis, los muertos. Ningún módulo estaba por debajo del 60 % del Gate 3, ni siquiera antes. Lo que
sobrevivía no era ruido: había una repetición posible de una prueba DPoP y un token de una persona que podía decir ser
del motor. Están abajo.

**Lo que cuesta medirlo:**
- **La pasada entera:** unos 80 minutos con 8 procesos, en una máquina que hacía otras cosas.
- **Lo que sobrevivía, con las pruebas nuevas:** 29 minutos. Una prueba nueva no resucita un mutante que ya estaba
  muerto, así que basta con volver a correr esos.

**Diez mutantes de `token_limits.py` no tienen veredicto:**
- **Qué rompen:** el constructor de `TokenLimits`, que el módulo llama al importarse (`LIMITS = TokenLimits()`).
- **Qué pasa:** pytest no llega a recoger las pruebas y termina con el código 4, que el script no cuenta.
- **En la práctica:** están detectados, porque con ellos no arranca nada. Contándolos, el módulo tiene un 93,5 % antes
  y un 98,1 % después.

**`src/auth/request_limits.py` no está en la tabla.** Llegó a `develop` con HIG-139 mientras se medía. Cae en
`src/auth/*`, así que el workflow ya lo mide.

## Las pruebas que se añadieron

Cada una mata mutantes que sobrevivían y comprueba algo que antes no se comprobaba.

**Lo que cambiaba la seguridad:**
- **Una prueba DPoP se podía repetir en su último segundo.** El jti de una prueba se recuerda hasta 65 s después de
  hecha, y la prueba vale hasta 60 s después. Con 55 s de memoria, nada fallaba.
  - **La prueba:** `test_dpop.py::test_a_proof_cannot_be_taken_again_for_as_long_as_it_could_be_taken`.
- **Un token de una persona que dijera ser del motor.** Que dijera tener un worker, o el rol `service`, se aceptaba si
  la condición perdía un `or`.
  - **La prueba:** `test_authentication.py::test_a_token_of_a_person_claims_no_key_no_worker_and_not_the_engine`.
- **Un jti vacío o de más de 128 caracteres** en una prueba DPoP, y un jti o un `cnf.jkt` que no fueran texto en un
  token de acceso.
  - **Las pruebas:** `test_dpop.py::test_the_window_of_a_proof_and_its_jti_are_checked_to_the_second` y
    `test_authentication.py::test_an_access_token_is_rejected_for_the_reason_it_should_be_before_and_after_its_claims`.
- **Un token o una prueba de más del tamaño máximo** se habría leído igual.
  - **Las pruebas:** las mismas dos.
- **Hacer sitio en los límites de `/oauth/token`.** Al llenarse, se pueden olvidar dos cosas: las ventanas que ya
  pasaron en vez de las vivas, o el origen más antiguo al contar uno que ya estaba. Así se olvida al origen que sigue
  fallando.
  - **Las pruebas:** `test_token_limits.py::test_making_room_drops_what_expired_before_an_origin_that_is_still_failing`
    y `test_token_limits.py::test_the_limits_hold_as_many_origins_as_they_may_and_counting_one_of_them_forgets_none`.
- **El índice ciego.** Debe distinguir campos y no cambiar de una versión a otra: si cambia, cada contacto guardado deja
  de encontrarse, sin un error.
  - **Las pruebas:** `test_client_encryption.py::test_the_blind_index_of_a_value_differs_between_fields` y
    `test_client_encryption.py::test_the_blind_index_is_the_same_from_one_version_to_the_next`, con valores fijos.

**Los bordes, cada uno en su lado:**
- **La vida de un token de acceso:** un segundo vale; cero no; la tolerancia del reloj, exacta.
  - **La prueba:** `test_authentication.py::test_the_lifetime_of_an_access_token_is_checked_to_the_second`.
- **La ventana de una prueba DPoP** y el segundo en que arrancó el proceso.
  - **La prueba:** `test_dpop.py::test_the_window_of_a_proof_and_its_jti_are_checked_to_the_second`.
- **El jti que se libera al acabar su ventana.**
  - **La prueba:** `test_dpop.py::test_a_jti_is_remembered_until_its_window_is_over_and_not_a_moment_more`.
- **El `Retry-After`:** cuenta hasta que sale la petición más antigua, y nunca baja de un segundo.
  - **La prueba:** `test_token_limits.py::test_the_wait_is_until_the_oldest_request_leaves_the_window_and_never_less_than_a_second`.

**Lo que va al log o a la respuesta:**
- **El motivo de cada rechazo de un token, de una prueba o de un intercambio:** son textos fijos para el log, y nada
  comprobaba la mitad.
  - **Las pruebas:** `test_authentication.py::test_the_log_says_why_a_token_or_a_request_was_turned_away`,
    `test_people.py::test_the_log_says_why_an_exchange_was_turned_away` y
    `test_dpop.py::test_a_proof_is_turned_away_for_the_reason_it_should_be_before_it_is_read`.
- **El texto del `422`** para cada clave mal formada.
  - **La prueba:** `test_client_registry.py::test_the_answer_says_what_is_wrong_with_the_key`.
- **El WARNING de cada movimiento del estado de un chatbot,** que es su auditoría, y que cada movimiento dice si
  ocurrió.
  - **Las pruebas:** `test_bot_state.py::test_the_warning_of_a_move_says_why_and_only_a_move_writes_one` y
    `test_bot_state.py::test_each_move_says_whether_it_happened_and_asks_nothing_when_it_cannot_happen`.
- **Que lo que no se abre dice solo su campo.**
  - **La prueba:** `test_client_encryption.py::test_what_does_not_open_says_only_which_field`.
- **Que `issue()` no firma un token que no es ni de una clave ni de una persona con su rol,** y que firma con el reloj
  que se le da.
  - **La prueba:** `test_authentication.py::test_a_token_is_issued_for_a_key_or_for_a_person_with_their_role_and_at_the_time_it_is_told`.

## Los que sobreviven a propósito

Doce. Diez son equivalentes: ninguna prueba puede distinguirlos, porque el código hace lo mismo.

| Mutante | El cambio | Por qué sobrevive |
|---|---|---|
| `auth.dpop.x_token_hash__mutmut_7` | `encode("ascii")` → `encode("ASCII")` | Python no distingue mayúsculas en el nombre de una codificación |
| `auth.dpop.x_token_hash__mutmut_8` | `rstrip(b"=")` → `rstrip(b"XX=XX")` | El último carácter de un SHA-256 en base64url codifica 4 bits y dos ceros: nunca es una `X` |
| `auth.access_tokens.x_issue__mutmut_58` | `alg=SIGNING_ALGORITHM` → `alg=None` | jwcrypto toma el algoritmo de la cabecera protegida, que lo dice igual |
| `auth.access_tokens.x_issue__mutmut_61` | sin `alg=` | Lo mismo |
| `auth.principal.x_exchange__mutmut_52` | `jti=claims.jti` → `jti=None` | El `Principal` del intercambio no se usa para nada que lea su jti: el token ya está firmado |
| `auth.principal.x_exchange_id_token__mutmut_28` | `jti=""` → `jti=None` | Lo mismo, en el intercambio de una persona |
| `auth.principal.x_exchange_id_token__mutmut_37` | `jti=""` → `jti="XXXX"` | Lo mismo |
| `auth.token_limits.x_origin_of__mutmut_5` | `candidate or ""` → `candidate or "XXXX"` | Ninguna de las dos es una dirección: las dos pasan al siguiente candidato |
| `auth.token_limits.x_origin_of__mutmut_13` | `strict=False` → `strict=None` | `ip_network` solo mira si es verdadero |
| `crypto.fields.xǁClientCipherǁ__init____mutmut_16` | sin `salt=None` | `None` es el valor por omisión de HKDF |

Los otros dos se aceptan:

| Mutante | El cambio | Por qué se acepta |
|---|---|---|
| `auth.access_tokens.x_read__mutmut_4` | `len(raw) > MAX_TOKEN_SIZE` → `>=` | Es el borde de un límite contra la denegación de servicio. Que lo que mide justo 8 192 caracteres pase o no da igual a la defensa, y un token válido de esa longitud exacta no siempre se puede construir: base64 crece de 4 en 4 |
| `auth.dpop.x_check__mutmut_12` | `len(raw) > MAX_PROOF_SIZE` → `>=` | Lo mismo, con 4 096 |
