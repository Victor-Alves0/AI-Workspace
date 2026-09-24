# Updater do AI Workspace — ver updater.py. Docker CLI + compose + git + python3.
FROM docker:28-cli

RUN apk add --no-cache bash git python3 setpriv \
    && docker compose version

WORKDIR /opt/updater
COPY updater.py run-update.sh ./
RUN chmod 0755 run-update.sh

EXPOSE 8080
CMD ["python3", "/opt/updater/updater.py"]
