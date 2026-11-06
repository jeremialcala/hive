# Releases

* **Estado:** approved
* **Fecha:** 2026-11-05
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 1.2.0

Cómo sale una versión de Hive y cómo llega a producción. Lo decidió Jeremi el 2026-10-30 (HIG-35):

1. **Una release por cada tanda que se despliega.** Cuando se fusionan PRs y se pide desplegar, primero se corta la
   release y después se despliega. Pueden salir varias en un día.
2. **Producción es la última release.** Se despliega su etiqueta `vX.Y.Z`, no `develop`. Lo urgente va por `hotfix/*`.
3. **Las versiones no esperan a los gates.** La versión dice qué entra, y cada gate dice en su documento
   (`.ai-dlc/gates/`) qué está y qué falta. La 0.4.0 salió con el Gate 3 abierto.

## Qué número le toca

[SemVer 2.0.0](https://semver.org/lang/es/), mirando el `[Unreleased]` del [CHANGELOG](../../CHANGELOG.md):

| Lo que hay en `[Unreleased]` | Versión |
|---|---|
| Solo `Corregido` o `Seguridad` que no cambian contratos | PARCHE: 0.4.0 → 0.4.1 |
| Algo en `Añadido` o `Cambiado` | MENOR: 0.4.1 → 0.5.0 |
| Un **cambio incompatible** (de API, de eventos o de despliegue) | MENOR mientras sea 0.x, con el aviso en su entrada; MAYOR desde la 1.0.0 |

## Cortar la release

Desde `develop` al día:

```sh
git fetch hive
git switch -c release/X.Y.Z hive/develop
python .ai-dlc/scripts/cortar_release.py X.Y.Z          # [Unreleased] pasa a [X.Y.Z] con la fecha de hoy
GATEWAY_URLS= python .ai-dlc/scripts/gen_openapi.py     # los contratos dicen la versión: X.Y.Z, sin «+develop»
git commit -am "chore: release X.Y.Z"
git push -u hive release/X.Y.Z
gh pr create --base main --head release/X.Y.Z --title "release X.Y.Z"
gh pr create --base develop --head release/X.Y.Z --title "release X.Y.Z, de vuelta a develop"
```

- **El script no escribe nada si algo no cuadra:** una versión que ya existe o que no es mayor que la última, o un
  `[Unreleased]` vacío.
- **Los dos PR salen de la misma rama:** `GitFlow guard` deja llegar `release/*` a `main`, y el segundo deja el corte
  del CHANGELOG también en `develop`.
- **Jeremi fusiona los dos.** Si entre medias entra algo más en `develop`, se queda en `[Unreleased]` para la
  siguiente.

Después del merge a `main`, la etiqueta va sobre su commit de merge:

```sh
git fetch hive
git tag -a vX.Y.Z hive/main -m "Hive X.Y.Z"
git push hive vX.Y.Z
```

**La etiqueta dispara el workflow `release`** (desde el 2026-11-05, [ADR-0036](../00-project/adr/0036-imagenes-firmadas-desde-el-ci.md)).
Por cada una de las cuatro imágenes:

1. comprueba que el CHANGELOG tiene la versión;
2. construye la imagen y la escanea con el umbral de HIG-157. Si rompe, no se publica nada;
3. genera su SBOM;
4. la publica en ghcr como `vX.Y.Z`;
5. la firma con cosign sin claves, y atesta el SBOM y la procedencia SLSA;
6. comprueba la firma.

Con las cuatro firmadas, **crea la release de GitHub**: las notas del CHANGELOG, el digest de cada imagen, el comando
para verificarlas y los SBOM adjuntos. Ya no se crea a mano. Se sigue en Actions; si falla, no hay release, y se
arregla con un `hotfix/*`.

El merge a `main` también ejecuta `build` y `build-engine`, que publican en GHCR `hive-gateway:sha-<7>` y
`hive-engine:sha-<7>`. Producción no los usa: se despliega desde las imágenes firmadas de la release.

## Desplegar la release

Solo con el «despliega» de Jeremi en ese turno, y con el
[runbook de despliegue y vuelta atrás](runbook-despliegue-y-rollback.md) (HIG-20). Desde la 0.20.0, ya no se
construye nada en el servidor: `deploy/desplegar.sh` descarga las imágenes firmadas de la release por su digest,
verifica su firma con `cosign verify` y solo entonces las levanta:

```sh
ssh -i ~/.ssh/id_ed25519_hgtech001 jalcala@192.168.10.2 'sudo bash -s -- vX.Y.Z' < deploy/desplegar.sh
```

Si los diez contenedores no quedan `healthy`, vuelve solo a la versión que corría. Las comprobaciones de después, los
disparadores de la vuelta atrás y lo que no se deshace están en el runbook.

## Volver atrás

Volver es desplegar la versión anterior con el mismo script; las anteriores a la 0.20.0, que no están firmadas, con
`--local`. El [runbook](runbook-despliegue-y-rollback.md#volver-atrás) tiene también cómo volver a mano si GitHub no
responde. Un cambio de Bifröst se revierte con la copia que `instalar.sh` deja en `/root/bifrost-antes-*`.

## Hotfix

Un arreglo que no puede esperar a lo que hay en `develop`:

1. `git switch -c hotfix/X.Y.Z+1 hive/main` y el arreglo con su entrada en `[Unreleased]`.
2. Se corta igual: `cortar_release.py X.Y.Z+1`.
3. Dos PR, uno a `main` y otro a `develop`.
4. Se etiqueta y se despliega como cualquier release.
