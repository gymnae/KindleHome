FROM python:3.12-alpine
RUN apk add --no-cache tzdata
WORKDIR /app
COPY server/kindle_vikunja.py .
USER nobody
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:${LISTEN_PORT:-8080}/healthz || exit 1
CMD ["python", "-u", "kindle_vikunja.py"]
