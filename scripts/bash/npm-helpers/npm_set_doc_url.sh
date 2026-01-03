#!/bin/bash
set -e

PROJECT_DIR="${1:?Missing arg #1 specifying a project source directory}"
PROJECT_URL="${2:?Missing arg #2 pointing project url. It is provided by CI_PROJECT_URL variable}"
FEATURE_BRANCH_NAME="${3:?Missing arg #3 pointing a branch name}"
FINAL_MERGE="${4:?Missing arg #4 pointing the final merge flag value}"
REPLACE_ENV_NAME="${5:-GEN_DOC_URL}"
REPLACE_FILE_PATH="${6:-${PROJECT_DIR}/README.md}"

pushd "${PROJECT_DIR}"

if [ "${FINAL_MERGE}" = "true" ]; then
  FINAL_MERGE=1
else
  FINAL_MERGE=0
fi

if [ ${FINAL_MERGE} -eq 1 ]; then
  DOC_URL="${PROJECT_URL}/-/wikis/home"
else
  DOC_URL="${PROJECT_URL}/-/wikis/non-stable/${FEATURE_BRANCH_NAME}/home"
fi

echo "Documentation url: ${DOC_URL}"

sed -i "s<\${${REPLACE_ENV_NAME}}<${DOC_URL}<g" "${REPLACE_FILE_PATH}"
if grep -q "\${${REPLACE_ENV_NAME}}" "${REPLACE_FILE_PATH}"; then
  echo "Failed to replace the documentation url in ${REPLACE_FILE_PATH}"
  exit 1;
fi # Ensure that the placeholder was replaced

echo "Replaced the documentation url: \"\${${REPLACE_ENV_NAME}}\" => \"${DOC_URL}\" in file: \"${REPLACE_FILE_PATH}\""

popd

echo ${REPLACE_ENV_NAME}="${DOC_URL}" > "gen_doc.env"
