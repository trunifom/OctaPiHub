# OctaPiHub als Container (optional; der empfohlene Weg ist der systemd-Dienst, siehe deploy/INSTALL.md).
# Bluetooth, Sense HAT und Audio brauchen Zugriff auf das Gerät. Siehe docs/DOCKER.md.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends bluez mpv \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --home-dir /var/lib/octapihub octapihub

WORKDIR /opt/octapihub
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[music]"

USER octapihub
VOLUME ["/var/lib/octapihub", "/etc/octapihub"]
ENV OCTAPIHUB_HOST=0.0.0.0 OCTAPIHUB_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
    CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:'+os.environ['OCTAPIHUB_PORT']+'/healthz', timeout=4)"
CMD ["python", "-m", "octapihub.web", "--config", "/etc/octapihub/octapihub.toml", "--secrets", "/etc/octapihub/secrets.env", "--data-dir", "/var/lib/octapihub"]
