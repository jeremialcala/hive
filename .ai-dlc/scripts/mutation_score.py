"""Puntuación de mutation testing por módulo, a partir de lo que `mutmut run` deja en mutants/ (HIG-137, Gate 3).

Uso:  python .ai-dlc/scripts/mutation_score.py [carpeta mutants] [--objetivo 60]

mutmut guarda, junto a cada fichero mutado, un `<fichero>.py.meta` con el código de salida de las pruebas para cada
mutante. `mutmut results` solo lista los que sobreviven, y `mutmut export-cicd-stats` lo suma todo en un número: aquí
se cuenta por módulo, que es lo que registra docs/04-testing/mutation-testing.md.

La puntuación es la habitual (la de Stryker): detectados entre los que cuentan. Un mutante detectado es uno que las
pruebas matan, o que las cuelga hasta el timeout; los que cuentan son esos más los que sobreviven y los que no
toca ninguna prueba. Un mutante sin pruebas no se descuenta: es justo lo que la puntuación tiene que enseñar.

No rompe el build por una puntuación baja: el 60 % del Gate 3 es un objetivo, y por debajo deja un aviso. Sí falla
si no hay nada que medir, para que un `mutmut run` roto no pase por una medición.
"""
import json
import pathlib
import sys

OBJETIVO = 60.0

# Los códigos de salida que mutmut 3 guarda, por lo que significan (mutmut/stats.py, status_by_exit_code).
MUERTO = {1, 3}
SOBREVIVE = {0}
SIN_PRUEBAS = {5, 33}
# mutmut cuenta también el 255 como timeout, pero con process_isolation = forkserver es el -1 de un worker al que
# pytest ni siquiera arrancó (una prueba que ya no existe con ese nombre, por ejemplo). Aquí va a «otros», con su
# aviso: contarlo como detectado inflaba la primera medición.
TIMEOUT = {36, -24, 24, 152}


def contar(codigos) -> dict:
    """Cuántos mutantes de un módulo hay en cada estado. Lo que no es ninguno de los cuatro, aparte."""
    cuenta = {"muertos": 0, "timeout": 0, "sobreviven": 0, "sin_pruebas": 0, "otros": 0, "sin_correr": 0}
    for codigo in codigos:
        if codigo is None:
            cuenta["sin_correr"] += 1
        elif codigo in MUERTO:
            cuenta["muertos"] += 1
        elif codigo in TIMEOUT:
            cuenta["timeout"] += 1
        elif codigo in SOBREVIVE:
            cuenta["sobreviven"] += 1
        elif codigo in SIN_PRUEBAS:
            cuenta["sin_pruebas"] += 1
        else:
            cuenta["otros"] += 1
    return cuenta


def puntuacion(cuenta: dict) -> float | None:
    detectados = cuenta["muertos"] + cuenta["timeout"]
    cuentan = detectados + cuenta["sobreviven"] + cuenta["sin_pruebas"]
    return 100.0 * detectados / cuentan if cuentan else None


def modulos(carpeta: pathlib.Path) -> dict:
    """La cuenta de cada módulo mutado, por su ruta en el repositorio (src/auth/dpop.py)."""
    resultado = {}
    for meta in sorted(carpeta.rglob("*.py.meta")):
        codigos = json.loads(meta.read_text(encoding="utf-8")).get("exit_code_by_key", {})
        if codigos:
            resultado[meta.relative_to(carpeta).as_posix()[:-len(".meta")]] = contar(codigos.values())
    return resultado


def tabla(cuentas: dict) -> list[str]:
    filas = ["| Módulo | Mutantes | Muertos | Timeout | Sobreviven | Sin pruebas | Sin veredicto | Puntuación |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    total = {clave: 0 for clave in contar([])}
    for modulo, cuenta in cuentas.items():
        for clave, valor in cuenta.items():
            total[clave] += valor
        filas.append(fila(f"`{modulo}`", cuenta))
    filas.append(fila("**Total**", total))
    return filas


def fila(nombre: str, cuenta: dict) -> str:
    valor = puntuacion(cuenta)
    texto = "—" if valor is None else f"{valor:.1f} %"
    return (f"| {nombre} | {sum(cuenta.values())} | {cuenta['muertos']} | {cuenta['timeout']} "
            f"| {cuenta['sobreviven']} | {cuenta['sin_pruebas']} | {cuenta['otros'] + cuenta['sin_correr']} "
            f"| {texto} |")


def main(argumentos: list[str]) -> int:
    objetivo = OBJETIVO
    if "--objetivo" in argumentos:
        posicion = argumentos.index("--objetivo")
        objetivo = float(argumentos[posicion + 1])
        argumentos = argumentos[:posicion] + argumentos[posicion + 2:]
    carpeta = pathlib.Path(argumentos[0] if argumentos else "mutants")
    cuentas = modulos(carpeta)
    if not cuentas:
        print(f"::error::no hay resultados de mutmut en {carpeta}: nada que medir")
        return 2
    print("\n".join(tabla(cuentas)))
    for modulo, cuenta in cuentas.items():
        valor = puntuacion(cuenta)
        if cuenta["sin_correr"]:
            print(f"::warning::{modulo}: {cuenta['sin_correr']} mutantes sin correr")
        if cuenta["otros"]:
            print(f"::warning::{modulo}: {cuenta['otros']} mutantes sin veredicto (saltados, sospechosos o en error);"
                  " no cuentan en la puntuación")
        if valor is not None and valor < objetivo:
            print(f"::warning::{modulo}: {valor:.1f} %, por debajo del objetivo del {objetivo:g} %")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
