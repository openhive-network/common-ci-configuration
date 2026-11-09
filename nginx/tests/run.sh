#!/usr/bin/env bash
# Tests the hive.rpc_form module of the nginx image: builds the image from
# Dockerfile.nginx, starts the stack of docker-compose.yml (the fixture API in
# PostgreSQL, PostgREST, the rewriter in front) and runs the pytest suite of this
# directory against it.
#
#   nginx/tests/run.sh [pytest args...]
#
# RESULTS_DIR   where junit.xml, the generated rpc_arguments.lua and the stack's
#               logs go (default: a new temporary directory)
# PUBLISH_ADDRESS  the address the stack's ports are published on (default 127.0.0.1;
#               0.0.0.0 under DinD, where the tests reach them on the daemon's host)
#
# The PostgREST and PostgreSQL versions are docker-bake.hcl's POSTGREST_VERSION and
# PSQL_VERSION, the ones the apps' images are built with.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
results="${RESULTS_DIR:-$(mktemp -d)}"
mkdir -p "$results"

bake_default() {
    sed -n "/^variable \"$1\"/,/^}/s/^ *default = \"\(.*\)\"/\1/p" "$root/docker-bake.hcl"
}

project="rpc-form-test-$$"
POSTGREST_VERSION="$(bake_default POSTGREST_VERSION)"
POSTGRES_IMAGE="postgres:$(bake_default PSQL_VERSION)"
NGINX_IMAGE="$project-nginx:local"
export POSTGREST_VERSION POSTGRES_IMAGE NGINX_IMAGE

compose() {
    docker compose -p "$project" -f "$here/docker-compose.yml" "$@"
}

cleanup() {
    compose logs --no-color > "$results/stack.log" 2>&1 || true
    compose down --volumes --rmi local > /dev/null 2>&1 || true
    docker image rm "$NGINX_IMAGE" > /dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -f "$root/Dockerfile.nginx" -t "$NGINX_IMAGE" "$root"
compose up --detach --build --wait

# The daemon's host: localhost, or the DinD service DOCKER_HOST names
daemon_host="$(sed -n 's|^tcp://\([^:/]*\).*|\1|p' <<< "${DOCKER_HOST:-}")"
daemon_host="${daemon_host:-127.0.0.1}"
published_port() {
    local address
    address="$(compose port "$1" "$2")"
    echo "${address##*:}"
}
REWRITER_URL="http://$daemon_host:$(published_port rewriter 80)"
POSTGREST_URL="http://$daemon_host:$(published_port postgrest 3000)"
RPC_ARGUMENTS="$results/rpc_arguments.lua"
export REWRITER_URL POSTGREST_URL RPC_ARGUMENTS
compose cp rewriter:/usr/local/openresty/nginx/conf/rpc_arguments.lua "$RPC_ARGUMENTS"

echo "Rewriter at $REWRITER_URL, PostgREST at $POSTGREST_URL, results in $results"
python3 -m pytest -p no:cacheprovider -rs --junitxml="$results/junit.xml" "$here" "$@"
