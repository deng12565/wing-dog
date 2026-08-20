ARG HERMES_BASE_IMAGE=nousresearch/hermes-agent@sha256:943935c838e6b839cda34b6e017142e6c157ad1615992f32287f37f532e07971
FROM ${HERMES_BASE_IMAGE}

USER root
COPY runtime/bootstrap.py /tmp/wing-dog-bootstrap.py
COPY deployment/linux/wheelhouse/ /tmp/wing-dog-wheelhouse/

RUN set -eu; \
    test -n "$(find /tmp/wing-dog-wheelhouse -maxdepth 1 -name '*.whl' -print -quit)"; \
    uv pip install --python /opt/hermes/.venv/bin/python \
        --no-config --no-index --find-links /tmp/wing-dog-wheelhouse \
        --no-deps --require-hashes \
        -r /tmp/wing-dog-wheelhouse/requirements-linux.lock; \
    /opt/hermes/.venv/bin/python /tmp/wing-dog-bootstrap.py \
        install-hermes-vision-patch \
        --agent-root /opt/hermes \
        --backup-dir /opt/hermes/.wing-dog-patches; \
    /opt/hermes/.venv/bin/python -c \
        'import importlib.metadata as m; expected={"brotli":"1.2.0","ddgs":"9.14.4","fake-useragent":"2.2.0","h2":"4.4.1","hpack":"4.2.0","hyperframe":"6.1.0","lxml":"6.1.2","primp":"1.3.1","pymysql":"1.1.1"}; assert {name:m.version(name) for name in expected} == expected; import ddgs, lxml, primp, pymysql'; \
    rm -rf /tmp/wing-dog-bootstrap.py /tmp/wing-dog-wheelhouse

LABEL org.opencontainers.image.title="Wing-Dog Hermes Runtime" \
      org.opencontainers.image.description="Pinned Hermes runtime with Wing-Dog dependencies and bounded vision patch"
