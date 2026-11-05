# ADR-0036: Las imágenes de cada release salen del CI, escaneadas y firmadas

* **Estado:** accepted
* **Fecha:** 2026-11-05
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 1.0.0
* **ID:** ADR-0036
* **Supersede:** el paso «se construye en el servidor desde la etiqueta» de
  [releases](../../05-deployment/releases.md)
* **Controles OWASP afectados:** A03 (la cadena de suministro: lo que corre es lo que pasó los gates), A08 (la
  integridad: firma y procedencia de cada imagen)
* **Requisitos:** RS12 y RF11 del [PRD](../../01-requirements/mvp-baas-gateway.md); HIG-160, HIG-20 (Gate 4)

## Contexto

Desde el 2026-10-30, producción es la última release ([releases](../../05-deployment/releases.md)). Se despliega así:

1. el operador empaqueta la etiqueta `vX.Y.Z` con `git archive`;
2. la copia a Nidavellir;
3. allí construye las cuatro imágenes: el gateway, el motor, el portal y la documentación.

Desde HIG-157, el CI escanea las imágenes en cada PR, y desde HIG-158 genera su SBOM. Pero las escanea en el runner
de GitHub: la imagen que corre en producción es **otra**, construida otro día en otra máquina.

- **Sin garantía de igualdad:** la base que se descargó, un `apk upgrade` o la caché de capas pueden hacer que no
  sean iguales.
- **Sin forma de comprobarlo:** nada permite saber, antes de levantarla, que la imagen viene de este repositorio y de
  esa etiqueta.

## Decisión

Jeremi lo decidió el 2026-11-05: **producción se despliega desde las imágenes que el CI construye, escanea y firma.**

1. **Un workflow, `release`, se dispara con cada etiqueta `vX.Y.Z`**, y antes comprueba que el CHANGELOG tiene esa
   versión. Por cada imagen:
   - **la construye** con el mismo Dockerfile que el resto del CI;
   - **la escanea** con el umbral de HIG-157: rompe con un CRITICAL o un HIGH con arreglo, y si rompe no se publica
     nada;
   - **genera su SBOM** CycloneDX (HIG-158);
   - **la publica** en ghcr como `ghcr.io/jeremialcala/hive-<imagen>:vX.Y.Z`;
   - **la firma con cosign sin claves.** El certificado sale del OIDC de GitHub y dice qué workflow, de qué
     repositorio y de qué etiqueta la firmó. No hay clave que guardar ni que se pueda robar. La firma queda en el
     registro público de Sigstore (Rekor);
   - **le atesta el SBOM** (`cosign attest --type cyclonedx`) **y la procedencia SLSA** (`attest-build-provenance`):
     qué commit, qué workflow y qué runner;
   - **comprueba su propia firma** antes de darla por buena.
2. **La release de GitHub la crea el mismo workflow**, cuando las cuatro imágenes están firmadas. Lleva las notas del
   CHANGELOG, el digest de cada imagen, el comando para verificarlas y los cuatro SBOM adjuntos. Antes se creaba a
   mano con `gh release create`.
3. **El servidor solo levanta lo verificado.** Hace el `pull` por digest y antes un `cosign verify` con esta identidad:

       --certificate-identity-regexp '^https://github.com/jeremialcala/hive/\.github/workflows/release\.yml@refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$'
       --certificate-oidc-issuer https://token.actions.githubusercontent.com

   Sin una firma válida, no se despliega. Eso es HIG-20, con el runbook.
4. **En un PR que cambia el workflow,** corre todo menos publicar, firmar y crear la release: construye, escanea y
   genera los SBOM. Lo demás se prueba con la primera etiqueta.

## Alternativas consideradas

- **Seguir construyendo en Nidavellir y firmar las etiquetas git.** Es lo que menos cambia. Pero la imagen que corre
  seguiría sin ser la que el CI escaneó, y la firma probaría el origen del código, no el de la imagen.
- **Firmar con una clave de cosign.** Habría que guardarla en GitHub y rotarla, y quien la robe firma lo que quiera.
  La firma sin claves ata la identidad al workflow de este repositorio, sin secreto.
- **Que `docker/build-push-action` genere él mismo la procedencia y el SBOM.** Lo hace, pero los publica como
  atestaciones de BuildKit, que `cosign verify-attestation` no lee igual. Se prefiere una sola herramienta para
  firmar y verificar.

## Consecuencias

- **Lo que corre es lo que pasó los gates,** y se puede comprobar en cualquier momento por su digest y su firma.
- **Una release necesita que el CI funcione.** Si GitHub o Sigstore no responden, no hay release, y lo urgente espera
  o va por `hotfix/*` con el mismo workflow.
- **El servidor necesita leer ghcr.** Si los paquetes son privados, necesita además un token de solo lectura. Lo
  resuelve HIG-20.
- **Las imágenes de ghcr pasan a ser las de producción,** con su etiqueta `vX.Y.Z`. Las que `build` y `build-engine`
  publican desde `main` con `sha-xxxxxxx` siguen para el despliegue continuo de antes, y no son las de producción.
- **El registro de Sigstore es público:** cada firma dice qué repositorio y qué etiqueta la produjo. El repositorio ya
  es público, así que no revela nada nuevo.
