# Contribuir

## Ramas (GitFlow)

- El trabajo sale de `develop` en ramas `feature/*` y vuelve a `develop` por pull request.
- `main` solo recibe pull requests desde `develop`, `release/*` o `hotfix/*`. Lo comprueba el workflow
  `GitFlow guard` y lo exige el ruleset que protege `main`.
- Para fusionar en `develop` o en `main` tienen que pasar los ocho controles que exigen los rulesets
  `Protect-DEVELOP` y `Protect-MAIN`: `gateway · test`, `gateway · image`, `gateway · docs`, `engine · test`, `engine · image`, `Fuga de secretos`, `SCA · dependencias vulnerables` y `SAST · CodeQL`. En `main` se exige además
  `GitFlow: origen permitido hacia main`. Los dos workflows de PR, `ci` y `engine`, corren en todo pull request: un
  control obligatorio que no corre deja el PR esperando para siempre. Un job nuevo que deba bloquear necesita un
  nombre que no repita ningún otro y una entrada en los dos rulesets.
- Un merge a `main` construye la imagen y la despliega: ver la sección Despliegue del [README](README.md).

## Flujo: Red-Green-Refactor-Secure

1. **Red** — escribe la prueba que falla (incluye los casos de abuso).
2. **Green** — implementa lo mínimo para pasar.
3. **Refactor** — limpia respetando SOLID / Clean Architecture.
4. **Secure** — revisa los controles OWASP aplicables (`.ai-dlc/owasp-mapping.md`).

Las pruebas se ejecutan con `pytest` desde la raíz y no necesitan servicios externos ni `config.env`.

## Dependencias

Las dependencias se declaran como rangos en `requirements.txt` (ejecución) y `requirements-dev.txt` (pruebas), y
se fijan con hashes en sus `.lock`. Para añadir, quitar o actualizar una:

```bash
pip install uv
uv pip compile --universal --python-version 3.12 --generate-hashes requirements.txt -o requirements.lock
uv pip compile --universal --python-version 3.12 --generate-hashes requirements-dev.txt -o requirements-dev.lock
pip install --require-hashes -r requirements-dev.lock
python .ai-dlc/scripts/gen_openapi.py
```

Los `.lock` no se editan a mano. El último paso regenera el contrato OpenAPI, que puede cambiar con la versión del
framework. El CI falla si un lock no fija los mismos paquetes que se declaran.

**Bórralo antes de regenerarlo.** Si el `.lock` ya existe, `uv pip compile` reutiliza sus versiones como preferencia.
El CI compila desde cero, así que un lock regenerado encima del anterior puede fijar paquetes distintos y romper esa
comprobación.

El motor de respuestas, en `engine/` (ADR-0023), tiene sus propios `requirements` y locks. Se regeneran igual, desde
`engine/`. La imagen del motor instala solo ruedas: una dependencia que pida compilarse rompe su construcción.

```bash
cd engine
rm -f requirements.lock requirements-dev.lock
uv pip compile --universal --python-version 3.12 --generate-hashes requirements.txt -o requirements.lock
uv pip compile --universal --python-version 3.12 --generate-hashes requirements-dev.txt -o requirements-dev.lock
```

## Cobertura

El CI exige una cobertura por ramas del 80 %, que es lo que pide el Gate 2, y rompe el build por debajo.

## Acceso a datos

Los datos de un cliente (chatbots, números, reglas, intents y contactos) solo se leen y se escriben a través de
`src/repository`, que exige el cliente y lo añade a cada consulta
([ADR-0012](docs/00-project/adr/0012-aislamiento-por-cliente-en-la-capa-de-datos.md)). Un controlador no llama a
`.objects` ni a `.save()`: recibe el repositorio y se lo pide a él. `tests/unit/test_data_access_boundary.py`
lee el código y falla si algo se lo salta. Los campos cifrados (números, credenciales de Meta y token de
verificación) están siempre cifrados en el documento: quien necesita el valor se lo pide al repositorio, y un
campo nuevo de ese tipo entra en la migración y en la prueba que vuelca la base. Una ruta nueva tiene que
entrar en
`tests/unit/test_client_isolation.py`, que también falla si no la cubre.

## Gates de seguridad

El workflow `Seguridad` rompe el build por tres motivos: un secreto en cualquier rama (gitleaks), una dependencia
con una vulnerabilidad conocida (`pip-audit`), o un hallazgo de seguridad alto o crítico de CodeQL fuera de
`tests/`. Dos cosas que conviene saber:

- **gitleaks revisa todas las ramas y toda la historia.** Un valor con aspecto de secreto en una rama cualquiera
  hace fallar todos los pull requests hasta que esa rama desaparece, y corregirlo en un commit posterior no
  basta. En las pruebas, los secretos de mentira tienen que parecer de mentira: `app-secret-of-this-test`, no una
  cadena hexadecimal.
- **Un hallazgo alto en `tests/` no bloquea**, pero sale como aviso y como alerta. Si es inherente a la prueba,
  se descarta en GitHub con el motivo "used in tests".

## Dual review

Toda PR requiere revisión humana más análisis asistido por IA.

## Gates

Cada fase cierra su gate (`.ai-dlc/gates/`) antes de avanzar. Un criterio sin evidencia se deja sin marcar.

## Commits y changelog

- Commits: `tipo(scope): mensaje` (`feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `sec`).
- Todo cambio notable añade su entrada en la sección `[Unreleased]` de [CHANGELOG.md](CHANGELOG.md).
- Las versiones siguen SemVer a partir de `v0.0.1`. Cada gate cerrado corta una versión menor: la `0.1.0` con el
  Gate 0, la `0.2.0` con el Gate 1 y la `0.3.0` con el Gate 2.

## Idioma

La documentación se escribe en español: `docs/`, `.ai-dlc/`, el changelog y el README. Los mensajes de commit
pueden ir en inglés. El código se escribe en inglés: identificadores, comentarios y mensajes de log.
