# -*- coding: utf-8 -*-
"""
    Corta una release en el CHANGELOG (docs/05-deployment/releases.md): lo que hay en [Unreleased] pasa a la versión
    nueva, con su fecha, y queda un [Unreleased] vacío encima. Los enlaces del final se ponen al día: [Unreleased]
    compara desde la etiqueta nueva y la versión nueva tiene el suyo.

        python .ai-dlc/scripts/cortar_release.py 0.4.0 [--fecha 2026-10-30] [--changelog CHANGELOG.md]
        python .ai-dlc/scripts/cortar_release.py 0.4.0 --notas      # las notas de la release, para GitHub

    Comprueba todo antes de escribir: que la versión es SemVer y mayor que la última, que no existe ya, que hay un
    solo [Unreleased] y que no está vacío. Conserva los finales de línea del fichero.
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

REPO = "https://github.com/jeremialcala/hive"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)
UNRELEASED_HEADING = "## [Unreleased]"
UNRELEASED_LINK = re.compile(r"^\[Unreleased\]: [^\r\n]*", re.MULTILINE)


def as_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def cut(text: str, version: str, day: str) -> str:
    """El CHANGELOG con la release cortada. Lanza ValueError con el motivo si algo no cuadra."""
    nl = "\r\n" if "\r\n" in text else "\n"
    if not SEMVER.fullmatch(version):
        raise ValueError(f"{version} no es una versión SemVer (MAYOR.MENOR.PARCHE)")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError(f"{day} no es una fecha ISO 8601 (AAAA-MM-DD)")
    if text.count(UNRELEASED_HEADING + nl) != 1:
        raise ValueError("el CHANGELOG debe tener exactamente un «## [Unreleased]»")
    versions = VERSION_HEADING.findall(text)
    if version in versions:
        raise ValueError(f"la {version} ya está en el CHANGELOG")
    if not versions:
        raise ValueError("no hay ninguna versión anterior con la que comparar")
    previous = versions[0]
    if as_tuple(version) <= as_tuple(previous):
        raise ValueError(f"la {version} no es mayor que la última, la {previous}")

    start = text.index(UNRELEASED_HEADING + nl) + len(UNRELEASED_HEADING + nl)
    end = text.index(f"## [{previous}]", start)
    if not text[start:end].strip():
        raise ValueError("[Unreleased] está vacío: no hay nada que publicar")

    text = text.replace(UNRELEASED_HEADING + nl, f"{UNRELEASED_HEADING}{nl}{nl}## [{version}] - {day}{nl}", 1)
    links = UNRELEASED_LINK.findall(text)
    if len(links) != 1:
        raise ValueError("el CHANGELOG debe tener exactamente un enlace «[Unreleased]: …» al final")
    # The match stops before the line's end, so each file keeps its own.
    new_links = f"[Unreleased]: {REPO}/compare/v{version}...HEAD{nl}[{version}]: {REPO}/compare/v{previous}...v{version}"
    return UNRELEASED_LINK.sub(lambda _: new_links, text, count=1)


def notes(text: str, version: str) -> str:
    """Lo que el CHANGELOG dice de una versión ya cortada, sin su título: las notas de su release en GitHub."""
    found = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[Unreleased\]: )", text,
                      re.MULTILINE | re.DOTALL)
    if found is None:
        raise ValueError(f"la {version} no está cortada en el CHANGELOG")
    return found.group(1).strip().replace("\r\n", "\n") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Corta una release en el CHANGELOG.")
    parser.add_argument("version")
    parser.add_argument("--fecha", default=date.today().isoformat())
    parser.add_argument("--changelog", default="CHANGELOG.md")
    parser.add_argument("--notas", action="store_true", help="no corta: escribe las notas de esa versión ya cortada")
    args = parser.parse_args()
    path = Path(args.changelog)
    with path.open(encoding="utf-8", newline="") as f:
        text = f.read()
    if args.notas:
        try:
            found = notes(text, args.version)
        except ValueError as e:
            print(f"Sin notas: {e}", file=sys.stderr)
            return 1
        # In UTF-8 whatever the console: on Windows it would be cp1252, which has no «→».
        sys.stdout.buffer.write(found.encode("utf-8"))
        return 0
    try:
        cut_text = cut(text, args.version, args.fecha)
    except ValueError as e:
        print(f"No se corta nada: {e}", file=sys.stderr)
        return 1
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(cut_text)
    print(f"Cortada la {args.version} ({args.fecha}) en {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
