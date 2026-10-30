# Releases

* **Estado:** approved
* **Fecha:** 2026-10-30
* **Decisores:** Jeremi Alcala
* **Fase AI-DLC:** 05-deployment
* **Versión:** 1.0.0

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

Después del merge a `main`, la etiqueta va sobre su commit de merge, con las notas que da el propio CHANGELOG:

```sh
git fetch hive
git tag -a vX.Y.Z hive/main -m "Hive X.Y.Z"
git push hive vX.Y.Z
python .ai-dlc/scripts/cortar_release.py X.Y.Z --notas > notas.md
gh release create vX.Y.Z --title "Hive X.Y.Z" --notes-file notas.md --verify-tag
```

El merge a `main` también ejecuta `build` y `build-engine`, que publican en GHCR `hive-gateway:sha-<7>` y
`hive-engine:sha-<7>`. midgard no los usa, porque el receptor de despliegue continuo no tiene Hive. Eso es HIG-20.

## Desplegar la release

Solo con el «despliega» de Jeremi en ese turno. Se construye en midgard desde la etiqueta, y las imágenes llevan la
versión:

```sh
git -c core.autocrlf=false archive vX.Y.Z | ssh midgard 'rm -rf ~/hive-build && mkdir ~/hive-build && tar -x -C ~/hive-build'
# En midgard, en ~/hive-build:
sudo docker build -t ghcr.io/jeremialcala/hive-gateway:vX.Y.Z .
sudo docker build -f deploy/docs/Dockerfile -t ghcr.io/jeremialcala/hive-docs:vX.Y.Z .
sudo docker build -f engine/Dockerfile -t ghcr.io/jeremialcala/hive-engine:vX.Y.Z .
sudo docker build -f portal/Dockerfile -t ghcr.io/jeremialcala/hive-portal:vX.Y.Z .
```

Después, cada proyecto con su compose:

- **Comprobar el compose:** antes de levantar, `diff -q` entre el compose desplegado y el de `~/hive-build`. Si
  cambió, se guarda una copia y se instala el nuevo.
- **El gateway:** `cd /srv/apps/hive-gateway && sudo env IMAGE_TAG=vX.Y.Z docker compose up -d`.
- **El motor:** `ENGINE_IMAGE_TAG=vX.Y.Z` en su `.env` (con copia antes), y
  `sudo docker compose --project-directory /srv/apps/hive-engine up -d`.
- **El portal:** `cd /srv/apps/hive-portal && sudo env PORTAL_IMAGE_TAG=vX.Y.Z docker compose up -d`.
- **Bifröst,** solo si la release trae un cambio suyo, con su `instalar.sh` y su orden: primero el gateway, después
  Bifröst, después el motor y al final el portal.

Se comprueba que cada contenedor está `healthy` con su imagen `vX.Y.Z` y sin errores en el log. Si el portal cambió, se
comprueba también que Cloudflare no sirve una copia vieja: `curl -sI` de un módulo debe decir `no-cache`, y
`cf-cache-status` nunca `HIT`.

## Volver atrás

Las imágenes de la release anterior siguen en midgard. Se levanta cada proyecto con la etiqueta anterior: el mismo
`compose up -d` con `IMAGE_TAG`, `ENGINE_IMAGE_TAG` y `PORTAL_IMAGE_TAG` en `vX.Y.(Z-1)`. Un cambio de Bifröst se
revierte con la copia que `instalar.sh` deja en `/root/bifrost-antes-*`.

## Hotfix

Un arreglo que no puede esperar a lo que hay en `develop`:

1. `git switch -c hotfix/X.Y.Z+1 hive/main` y el arreglo con su entrada en `[Unreleased]`.
2. Se corta igual: `cortar_release.py X.Y.Z+1`.
3. Dos PR, uno a `main` y otro a `develop`.
4. Se etiqueta y se despliega como cualquier release.
