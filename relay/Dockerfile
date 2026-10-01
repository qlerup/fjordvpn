FROM python:3.13-alpine
RUN apk add --no-cache socat
WORKDIR /app
COPY observer.py /app/observer.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python", "observer.py"]
