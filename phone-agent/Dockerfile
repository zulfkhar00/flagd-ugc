FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && groupadd --system phone \
    && useradd --system --gid phone --home-dir /app phone \
    && mkdir -p /data/runs \
    && chown -R phone:phone /data

USER phone
VOLUME ["/data"]
EXPOSE 8787

CMD ["flagd-phone", "serve", "--bind", "0.0.0.0", "--port", "8787", \
     "--runs-dir", "/data/runs", "--control-socket", "/data/control.sock", \
     "--park-on-finish"]
