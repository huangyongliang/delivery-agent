FROM python:3.12-slim

WORKDIR /srv
COPY app.py .

ENV PRODUCT_NAME=sample-product \
    PYTHONUNBUFFERED=1

EXPOSE 8000
USER nobody
CMD ["python", "app.py"]
