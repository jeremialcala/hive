# La imagen del motor de respuestas (ADR-0023 y ADR-0024 del gateway: ghcr.io/jeremialcala/hive-engine). Se construye desde
# la raíz del monorepo, que es donde está `shared/`:
#
#   docker build -f engine/Dockerfile .
#
# Lo que entra en el contexto lo dice engine/Dockerfile.dockerignore, que BuildKit usa en lugar del .dockerignore
# de la raíz (ese deja fuera engine/, para la imagen del gateway).
#
# Una sola imagen para los seis workers (ADR-0008): cada contenedor dice cuál es, con su orden
# (`python -m src.workers.<worker>`). Sin NeMo ni annoy no se compila nada, así que basta una etapa, sobre la base
# delgada. Va por digest: un tag es móvil y la misma línea daría otra imagen otro día. Para renovarla:
#   docker buildx imagetools inspect python:3.12-slim --format '{{.Manifest.Digest}}'
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
LABEL authors="Jeremi"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Solo lo de ejecución, del lock y con hashes: nada se vuelve a resolver al construir. Solo ruedas: si algún día
# una dependencia pide compilarse, la construcción falla aquí en vez de traer un compilador a la imagen.
COPY engine/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.lock \
 && rm /tmp/requirements.lock

WORKDIR /usr/src/app

# Solo el código: ni las pruebas ni los docs, que además el .dockerignore deja fuera del contexto.
COPY engine/src/ ./src/
# Y lo que comparte con el gateway, importable como `shared`.
COPY shared/ ./shared/

# Corre sin privilegios, con el código de solo lectura (de root). El worker solo escribe su marca de vida en /tmp.
RUN useradd --system --uid 10001 --no-create-home appuser
USER appuser

# El clasificador, si el contenedor no dice otro worker.
CMD ["python", "-m", "src.workers.classifier"]
