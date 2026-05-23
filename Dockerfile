FROM python:3.12-slim
RUN useradd -m -u 10001 appuser
WORKDIR /app
COPY mc_failover_proxy.py config.example.toml ./
USER appuser
EXPOSE 25565
CMD ["python","mc_failover_proxy.py","--config","config.toml"]
