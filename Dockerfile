FROM python:3.11-slim

WORKDIR /app

COPY backchannel/ ./backchannel/
COPY docs/ ./docs/

RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "-m", "backchannel", "serve", "--host", "0.0.0.0", "--port", "8080", "--db", "/data/backchannel.db"]
