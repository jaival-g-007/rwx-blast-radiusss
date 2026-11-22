FROM docker.io/library/python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS upstream
COPY tools/extract_sources.py /tmp/extract_sources.py
RUN python -I /tmp/extract_sources.py /src

FROM docker.io/library/python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
WORKDIR /app
COPY --from=upstream /src /src
RUN python -m compileall -q /src/sqlglot /src/tests
COPY replay.py selector.py impact.py comparisons.py patches.py coverage.json operands.json lookup_keys.json ./
ENV PYTHONDONTWRITEBYTECODE=1
USER 65534:65534
CMD ["python", "/app/selector.py"]
