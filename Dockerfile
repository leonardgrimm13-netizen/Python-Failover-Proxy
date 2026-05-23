FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --shell /usr/sbin/nologin mcfailover

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

COPY mc_failover_proxy.py /app/mc_failover_proxy.py

USER mcfailover

EXPOSE 25565/tcp
EXPOSE 8080/tcp

CMD ["python", "/app/mc_failover_proxy.py", "--config", "/config/config.toml"]
