FROM python:3.13-alpine
RUN apk add --no-cache socat
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py configuration.py hub.py runtime.py store.py ./
COPY seedbox_export.py ./
COPY relay ./relay
COPY templates ./templates
COPY static ./static
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FJORDVPN_DATA=/data UI_BIND_IP=0.0.0.0 FJORDVPN_CONTAINER=1
EXPOSE 8088
CMD ["python", "app.py"]
