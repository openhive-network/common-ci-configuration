FROM docker:20.10.10 AS docker-builder

COPY scripts/bash/*.sh /usr/local/bin
RUN apk add --no-cache bash git ca-certificates curl

FROM python:3.11.0 AS image-remover

COPY scripts/python/delete-image.py /
COPY scripts/python/requirements.txt /
RUN pip install -r requirements.txt
CMD [ "bash" ]