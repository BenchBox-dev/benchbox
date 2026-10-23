FROM apache/spark:4.0.2-scala2.13-java17-python3-ubuntu

ARG GLUTEN_VERSION=1.6.0
ARG TARGETARCH

LABEL org.opencontainers.image.title="BenchBox Velox"
LABEL org.opencontainers.image.description="BenchBox + Apache Gluten + Velox on Spark 4.0.2"
LABEL org.opencontainers.image.version="${GLUTEN_VERSION}"

USER root

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

RUN set -eux; \
    SPARK_VER="$(grep '__version__' "${SPARK_HOME}/python/pyspark/version.py" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"; \
    [ -n "${SPARK_VER}" ] || { echo "FATAL: could not detect Spark version from PySpark"; exit 1; }; \
    echo "${SPARK_VER}" > /opt/spark/SPARK_VERSION; \
    echo "Detected Spark ${SPARK_VER}"; \
    SC_BASE_URL="https://repo1.maven.org/maven2/org/apache/spark/spark-connect_2.13/${SPARK_VER}"; \
    SC_JAR="spark-connect_2.13-${SPARK_VER}.jar"; \
    cd /opt/spark/jars; \
    curl -fsSL "${SC_BASE_URL}/${SC_JAR}" -o "${SC_JAR}"; \
    curl -fsSL "${SC_BASE_URL}/${SC_JAR}.sha1" -o "${SC_JAR}.sha1"; \
    echo "$(cat "${SC_JAR}.sha1")  ${SC_JAR}" | sha1sum -c -; \
    rm -f "${SC_JAR}.sha1"

RUN set -eux; \
    if [ "${TARGETARCH}" != "amd64" ]; then \
        echo "ERROR: Apache Gluten ${GLUTEN_VERSION} publishes only an amd64 release jar in the official Spark 4.0 tarball."; \
        echo "       TARGETARCH=${TARGETARCH} is unsupported by the checked-in Dockerfile."; \
        echo "       Use --platform linux/amd64, or provide a custom aarch64 jar outside this default workflow."; \
        exit 1; \
    fi; \
    JAR_NAME="gluten-velox-bundle-spark4.0_2.13-linux_amd64-${GLUTEN_VERSION}.jar"; \
    RELEASE_TARBALL="apache-gluten-${GLUTEN_VERSION}-bin-spark-4.0.tar.gz"; \
    MIRROR="https://downloads.apache.org/gluten/${GLUTEN_VERSION}"; \
    cd /tmp; \
    curl -fsSLO "${MIRROR}/${RELEASE_TARBALL}"; \
    curl -fsSLO "${MIRROR}/${RELEASE_TARBALL}.sha512"; \
    sha512sum -c "${RELEASE_TARBALL}.sha512"; \
    tar -xOf "${RELEASE_TARBALL}" "${JAR_NAME}" > /opt/gluten.jar; \
    rm -f "${RELEASE_TARBALL}" "${RELEASE_TARBALL}.sha512"; \
    echo "Extracted: ${JAR_NAME} -> /opt/gluten.jar"

COPY docker/velox/ZstdJniCodec.java /tmp/ZstdJniCodec.java
RUN set -eux; \
    mkdir -p /tmp/codec_build/io/benchbox/codec; \
    cp /tmp/ZstdJniCodec.java /tmp/codec_build/io/benchbox/codec/; \
    ZSTD_JNI_JAR="$(ls /opt/spark/jars/zstd-jni-*.jar 2>/dev/null | head -1)"; \
    HADOOP_API_JAR="$(ls /opt/spark/jars/hadoop-client-api-*.jar 2>/dev/null | head -1)"; \
    [ -n "${ZSTD_JNI_JAR}" ] || { echo "FATAL: zstd-jni-*.jar not found in /opt/spark/jars/"; exit 1; }; \
    [ -n "${HADOOP_API_JAR}" ] || { echo "FATAL: hadoop-client-api-*.jar not found in /opt/spark/jars/"; exit 1; }; \
    javac -cp "${ZSTD_JNI_JAR}:${HADOOP_API_JAR}" \
          -d /tmp/codec_build \
          /tmp/codec_build/io/benchbox/codec/ZstdJniCodec.java; \
    jar cf /opt/spark/jars/benchbox-zstd-codec.jar -C /tmp/codec_build .; \
    rm -rf /tmp/codec_build /tmp/ZstdJniCodec.java; \
    echo "Built: /opt/spark/jars/benchbox-zstd-codec.jar"

COPY --chown=spark:spark . /opt/benchbox
RUN uv pip install --system --no-cache "/opt/benchbox[velox]"

COPY docker/velox/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER spark

EXPOSE 50051

ENTRYPOINT ["/entrypoint.sh"]
CMD ["connect"]
