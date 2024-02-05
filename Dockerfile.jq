# syntax=docker/dockerfile:1.5
FROM alpine:3.19.1

RUN <<-EOF
    set -e

    apk update
    apk upgrade
    apk add --no-cache bash curl jq
    rm -rf /tmp/* /var/cache/apk/*
EOF

CMD [ "/bin/bash" ]