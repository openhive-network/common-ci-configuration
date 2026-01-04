#!/bin/bash
#
# Creates a HAF application role on a PostgreSQL cluster
# Fetched from: common-ci-configuration/haf-app-tools/scripts/create_haf_app_role.sh
#

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

LOG_FILE=setup_postgres.log

# Source common.sh - try local first, then fetch from common-ci-configuration
if [[ -f "$SCRIPTPATH/common.sh" ]]; then
    source "$SCRIPTPATH/common.sh"
else
    COMMON_CI_URL="${COMMON_CI_URL:-https://gitlab.syncad.com/hive/common-ci-configuration/-/raw/develop}"
    COMMON_SH="/tmp/haf-app-tools-common.sh"
    if [[ ! -f "$COMMON_SH" ]]; then
        curl -fsSL "${COMMON_CI_URL}/haf-app-tools/scripts/common.sh" -o "$COMMON_SH"
    fi
    source "$COMMON_SH"
fi

log_exec_params "$@"

print_help () {
    echo "Usage: $0 [OPTION[=VALUE]]..."
    echo
    echo "Creates a HAF app role on a PostgreSQL cluster."
    echo "OPTIONS:"
    echo "  --host=VALUE              Specify postgreSQL host location (defaults to /var/run/postgresql)."
    echo "  --port=NUMBER             Specify a postgreSQL operating port (defaults to 5432)."
    echo "  --postgres-url=URL        Specify postgreSQL connection url directly."
    echo "  --haf-app-account=NAME    Specify an account name to be added to the base group."
    echo "  --base-group=GROUP        Specify the base group (defaults to hive_applications_owner_group)."
    echo "  --public                  Enable query_supervisor limiting for the haf_app_account."
    echo "  --help                    Display this help screen and exit."
    echo
}

create_haf_app_account() {
  local pg_access="$1"
  local haf_app_account="$2"
  local is_public="$3"

  local base_group="$BASE_GROUP"
  local alter_to_public=""
  $is_public && alter_to_public="ALTER ROLE ${haf_app_account} SET query_supervisor.limits_enabled TO true;"

  psql -aw "$pg_access" -v ON_ERROR_STOP=on -f - <<EOF
DO \$$
BEGIN
    BEGIN
      CREATE ROLE $haf_app_account WITH LOGIN INHERIT IN ROLE ${base_group};
      EXCEPTION WHEN DUPLICATE_OBJECT THEN
      RAISE NOTICE '$haf_app_account role already exists';
    END;
    ${alter_to_public}
END
\$$;

EOF

}

# Default values for variables
HAF_APP_ACCOUNT=""
POSTGRES_HOST="/var/run/postgresql"
POSTGRES_PORT=5432
POSTGRES_URL=""
PUBLIC=false
BASE_GROUP="hive_applications_owner_group"

# Parse command line arguments
while [ $# -gt 0 ]; do
  case "$1" in
    --host=*)
        POSTGRES_HOST="${1#*=}"
        ;;
    --port=*)
        POSTGRES_PORT="${1#*=}"
        ;;
    --postgres-url=*)
        POSTGRES_URL="${1#*=}"
        ;;
    --haf-app-account=*)
        HAF_APP_ACCOUNT="${1#*=}"
        ;;
    --base-group=*)
        BASE_GROUP="${1#*=}"
        ;;
    --public)
        PUBLIC=true
        ;;
    --help)
        print_help
        exit 0
        ;;
    -*)
        echo "ERROR: '$1' is not a valid option."
        echo
        print_help
        exit 1
        ;;
    *)
        echo "ERROR: '$1' is not a valid argument."
        echo
        print_help
        exit 2
        ;;
  esac
  shift
done

if [ -z "$POSTGRES_URL" ]; then
  POSTGRES_ACCESS="postgresql://?dbname=haf_block_log&port=${POSTGRES_PORT}&host=${POSTGRES_HOST}"
else
  POSTGRES_ACCESS=$POSTGRES_URL
fi

# Ensure that the haf app account is specified
_TST_HAF_APP_ACCOUNT=${HAF_APP_ACCOUNT:? "Missing application account name - it should be specified by using '--haf-app-account=name' option"}

echo $POSTGRES_ACCESS

create_haf_app_account "$POSTGRES_ACCESS" "$HAF_APP_ACCOUNT" ${PUBLIC}
