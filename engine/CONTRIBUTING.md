# Contribuir

## Ramas

`main` es la rama estable y `develop` la de integración. El trabajo sale de `develop` en ramas
`feature/`, `chore/` o `hotfix/` y vuelve por pull request. `develop` se integra en `main` por
pull request.

## Flujo: Red-Green-Refactor-Secure

1. **Red** — escribe el test que falla, incluidos los payloads de abuso del PRD (AB-01 a AB-12).
2. **Green** — implementa lo mínimo para pasar.
3. **Refactor** — limpia respetando la regla de dependencia: `application` y `domain` no
   importan de `infrastructure`.
4. **Secure** — revisa los controles de `.ai-dlc/owasp-mapping.md` que toca el cambio.

## Antes de abrir un pull request

```bash
pytest tests/ --cov=src --cov-branch
pylint --rcfile=pyproject.toml src
python ~/.claude/skills/ai-dlc/scripts/validate_mermaid.py docs   # si tocaste documentación
```

- Si cambia el sobre (`src/domain/models/sobre.py`), cambia también su contrato,
  `docs/02-design/contracts/asyncapi-motor.yaml`: las pruebas de contrato fallan si no coinciden.
- Una decisión de diseño nueva es una ADR nueva (`.ai-dlc/templates/adr.md`).
- Todo cambio notable lleva su línea en `CHANGELOG.md`, bajo `[Unreleased]`.

## Dual review

Todo pull request requiere revisión humana y análisis asistido por IA. El autor no aprueba su
propio pull request.

## Gates

Un pull request no se fusiona si deja abierto un criterio del gate de su fase
(`.ai-dlc/gates/`). Hoy esa comprobación es manual: el pipeline aún no la automatiza.

## Commits

Convención: `tipo(scope): mensaje` (feat, fix, docs, test, chore, refactor, sec).
