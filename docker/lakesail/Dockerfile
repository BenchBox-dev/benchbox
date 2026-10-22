FROM python:3.14-slim

ARG PYSAIL_VERSION=0.7.1
ARG PYSPARK_CLIENT_VERSION=4.2.0

ENV SPARK_HOME=/opt/pyspark-client-no-spark-home

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

RUN uv pip install --system --no-cache \
    "pysail==${PYSAIL_VERSION}" \
    "pyspark-client==${PYSPARK_CLIENT_VERSION}"

ENTRYPOINT ["/usr/local/bin/sail"]
