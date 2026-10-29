FROM ubuntu:24.04

ARG GH_VERSION=2.103.3
ARG GH_SHA256_AMD64=4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d
ARG GH_SHA256_ARM64=ee32df2323c8847f5fac6cb6b4b69ca75828e9329a8f22f677021f7b3306ce63

ENV DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=automatic

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        jq \
        make \
        pkg-config \
        systemd-sysv \
        tzdata \
        unzip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) sha="$GH_SHA256_AMD64" ;; \
        arm64) sha="$GH_SHA256_ARM64" ;; \
        *) echo "unsupported architecture: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/gh.tar.gz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz"; \
    echo "${sha}  /tmp/gh.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/gh.tar.gz -C /tmp; \
    install -m 0755 "/tmp/gh_${GH_VERSION}_linux_${arch}/bin/gh" /usr/local/bin/gh; \
    rm -rf /tmp/gh.tar.gz "/tmp/gh_${GH_VERSION}_linux_${arch}"

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /uvx /usr/local/bin/

CMD ["bash"]
