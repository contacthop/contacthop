# ContactHop harness image.
#   docker build -t contacthop .
#   docker run -p 8000:8000 contacthop
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache ".[postgres]"

FROM python:3.12-slim

RUN useradd --create-home --shell /usr/sbin/nologin contacthop
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

USER contacthop
WORKDIR /home/contacthop
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["contacthop", "serve", "--host", "0.0.0.0", "--port", "8000"]
