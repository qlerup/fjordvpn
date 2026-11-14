FROM python:3.13-alpine
RUN apk upgrade --no-cache && apk add --no-cache socat iproute2
WORKDIR /app
COPY observer.py /app/observer.py
COPY gateway_tcp.py /app/relay/gateway_tcp.py
COPY traffic.py /app/relay/traffic.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "observer.py"]
