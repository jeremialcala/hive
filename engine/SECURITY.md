# Política de Seguridad

## Reporte de vulnerabilidades

No abras un issue público para un fallo de seguridad. Usa el reporte privado de GitHub:
<https://github.com/jeremialcala/ms-python-metamsghandler/security/advisories/new>.

<TODO: confirmar que el reporte privado de vulnerabilidades está activado en el repositorio y
añadir un buzón de contacto>

## Estado conocido

El threat model (`docs/02-design/threat-model.md`) lista doce amenazas, varias con brechas
abiertas. Las de mayor puntuación: consumo no acotado (T7), prompt injection (T1), datos
personales en logs (T3) y pérdida silenciosa de mensajes (T6). El servicio no está desplegado.

## Gestión de secretos

Los secretos se leen de `config.env`, que está fuera del repositorio (`.gitignore`) y de la
imagen (`.dockerignore`). El gestor de secretos para los entornos desplegados está por elegir
(ver ADR-0006). Prohibido commitear secretos.

## Datos personales

El servicio trata identificadores de usuario (incluidos números de teléfono) y texto libre de
mensajes. Clasificación y regulación aplicable: `docs/00-project/data-classification.md`.

## SBOM y supply chain (A03)

Las dependencias tienen la versión fijada en `requirements.txt`. Todavía no se genera SBOM ni
se ejecuta SCA: son criterios de los gates 2 y 4.

## OWASP

Controles mapeados en `.ai-dlc/owasp-mapping.md` (Top 10:2025), con un anexo específico de
este servicio.
