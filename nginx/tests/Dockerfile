# The rewriter tests' stack (docker-compose.yml). process_openapi.py, run once on the
# fixture, writes the SQL the database runs and the rewrite rules and argument map
# the rewriter, built on the image of Dockerfile.nginx, loads.
ARG NGINX_IMAGE
ARG POSTGRES_IMAGE

FROM registry.gitlab.syncad.com/hive/common-ci-configuration/mirrors/python-3.12.9-slim-bookworm AS generate
COPY haf-app-tools/python/requirements.txt /tools/requirements.txt
RUN pip install --no-cache-dir -r /tools/requirements.txt
COPY haf-app-tools/python/process_openapi.py /tools/
COPY nginx/tests/fixture/rewriter_fixture.sql /generate/
WORKDIR /generate
# rewrite_rules.conf starts with the catch-all rule; an app puts it last, by reversing
# the file's rules (as their openapi_rewrite.sh does), so the specific ones match first.
RUN python /tools/process_openapi.py sql rewriter_fixture.sql \
    && awk 'BEGIN { RS = ""; ORS = "\n\n" } { rule[NR] = $0 } END { for (i = NR; i >= 1; i--) print rule[i] }' \
        rewrite_rules.conf > rewrite_rules.reversed

FROM ${POSTGRES_IMAGE} AS db
COPY --from=generate /generate/sql/rewriter_fixture.sql /docker-entrypoint-initdb.d/

FROM ${NGINX_IMAGE} AS rewriter
COPY nginx/tests/fixture/nginx.conf /usr/local/openresty/nginx/conf/nginx.conf
COPY --from=generate /generate/rewrite_rules.reversed /usr/local/openresty/nginx/conf/rewrite_rules.conf
COPY --from=generate /generate/rpc_arguments.lua /usr/local/openresty/nginx/conf/rpc_arguments.lua
