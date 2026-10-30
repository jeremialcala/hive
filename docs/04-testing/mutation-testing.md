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

**Tarda horas, no minutos.** Cada mutante de la autenticación que sobrevive corre las 1 600 pruebas que pasan por ella.
La medición de abajo tardó PENDIENTE con 8 procesos, y el job tiene 300 minutos.

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

PENDIENTE

## Las pruebas que se añadieron

Cada una mata mutantes que sobrevivían y dice algo que antes no se comprobaba:

PENDIENTE

## Los que sobreviven a propósito

PENDIENTE
