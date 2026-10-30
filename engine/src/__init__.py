"""
    El motor de respuestas: seis workers sobre Bifröst, de una misma imagen (ADR-0008).

        - domain:          el sobre que se pasan los workers y los puertos (interfaces).
        - infrastructure:  las implementaciones concretas: el LLM, el gateway, Meta, Bifröst y el saneado.
        - workers:         el punto de entrada de cada worker y el bucle que comparten.
"""
