#!/bin/bash

set -euo pipefail

GLUTEN_JAR="${GLUTEN_JAR:-/opt/gluten.jar}"
SPARK_DRIVER_MEM="${SPARK_DRIVER_MEM:-4g}"
VELOX_OFFHEAP="${VELOX_OFFHEAP:-8g}"
SPARK_CONNECT_PORT="${SPARK_CONNECT_PORT:-50051}"

if [[ -z "${SPARK_VERSION:-}" ]]; then
    if [[ -r /opt/spark/SPARK_VERSION ]]; then
        SPARK_VERSION="$(cat /opt/spark/SPARK_VERSION)"
    else
        echo "FATAL: SPARK_VERSION is unset and /opt/spark/SPARK_VERSION is not readable." >&2
        echo "       The Dockerfile is expected to write this file at build time." >&2
        exit 1
    fi
fi
if [[ -z "${SPARK_VERSION}" ]]; then
    echo "FATAL: SPARK_VERSION resolved to an empty string." >&2
    exit 1
fi
export SPARK_VERSION

GLUTEN_CONF=(
    "--conf" "spark.plugins=org.apache.gluten.GlutenPlugin"
    "--conf" "spark.memory.offHeap.enabled=true"
    "--conf" "spark.memory.offHeap.size=${VELOX_OFFHEAP}"
    "--conf" "spark.shuffle.manager=org.apache.spark.shuffle.sort.ColumnarShuffleManager"
    "--conf" "spark.driver.extraClassPath=${GLUTEN_JAR}"
    "--conf" "spark.executor.extraClassPath=${GLUTEN_JAR}"
    "--conf" "spark.driver.memory=${SPARK_DRIVER_MEM}"
    "--conf" "spark.sql.adaptive.enabled=true"
    "--conf" "spark.hadoop.io.compression.codecs=io.benchbox.codec.ZstdJniCodec"
    "--conf" "spark.sql.warehouse.dir=/tmp/spark-warehouse"
)

MODE="${1:-connect}"

case "${MODE}" in
  connect)
    echo "[velox-entrypoint] Starting Gluten-enabled Spark-Connect server on port ${SPARK_CONNECT_PORT}"
    echo "[velox-entrypoint] Gluten jar: ${GLUTEN_JAR}"
    echo "[velox-entrypoint] Off-heap:   ${VELOX_OFFHEAP}"
    echo "[velox-entrypoint] Spark:      ${SPARK_VERSION}"

    export SPARK_NO_DAEMONIZE=true
    exec "${SPARK_HOME}/sbin/start-connect-server.sh" \
        --master "local[*]" \
        --jars "${GLUTEN_JAR}" \
        "${GLUTEN_CONF[@]}" \
        "--conf" "spark.connect.grpc.binding.port=${SPARK_CONNECT_PORT}"
    ;;

  run)
    shift
    echo "[velox-entrypoint] Running benchbox inside container (local Gluten mode)"
    echo "[velox-entrypoint] Gluten jar: ${GLUTEN_JAR}"

    exec benchbox run \
        --platform velox:local \
        --platform-option "gluten_jar_path=${GLUTEN_JAR}" \
        --platform-option "offheap_size=${VELOX_OFFHEAP}" \
        "$@"
    ;;

  shell)
    exec /bin/bash
    ;;

  *)
    exec "$@"
    ;;
esac
