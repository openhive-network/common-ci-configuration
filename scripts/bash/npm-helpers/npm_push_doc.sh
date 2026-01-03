#! /bin/bash
set -e

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

perform_wiki_cleanup() {
  local nonstable_dir="${1}"
  local repo_url="${2}"

  echo "Attempting to perform feature-branch storage cleanup."

  pushd "${nonstable_dir}"

  for d in * ; do
    if [ -d "${d}" ]; then
      echo "Processing subdirectory ${d}"

      set +e
      git ls-remote --heads -q --exit-code "${repo_url}" "refs/heads/${d}"
      local retcode=$?
      set -e

      if [ ${retcode} -eq 0 ]; then
        echo "Branch ${d} exists, skipping"
      elif [ ${retcode} -eq 2 ]; then
        echo "Branch ${d} does not exist. Performing documentation storage cleanup"
        git rm -r "${d}"
        git commit -m "Cleanup actions done for docs placed in: ${d}".
        git push origin "HEAD:main"
      else
        echo "ERROR, git command failed"
        exit 1
      fi
    fi
  done

  popd

  echo "Cleanup done."
}

PROJECT_DIR="${1:?Missing arg #1 specifying a project source directory}"

PROJECT_URL="${2:?Missing arg #2 pointing project url. It is provided by CI_PROJECT_URL variable}"
PROJECT_ACCESS_TOKEN="${3:?Missing arg #3 pointing a Gitlab repository access token}"
DIST_DIR="${4:?Missing arg #4 pointing the dist directory}"
FEATURE_BRANCH_NAME="${5:?Missing arg #5 pointing a branch name}"
FINAL_MERGE="${6:?Missing arg #6 pointing the final merge flag value}"
DOC_URL="${7:?Missing arg #7 pointing the documentation URL}"

if [ "${FINAL_MERGE}" = "true" ]; then
  FINAL_MERGE=1
else
  FINAL_MERGE=0
fi


echo "Using project directory: ${PROJECT_DIR}"
echo "Using project URL: ${PROJECT_URL}"
echo "Using dist dir: ${DIST_DIR}"
echo "Does it is a final push for default/protected branch?: ${FINAL_MERGE}"

WIKI_REPO_URL="${PROJECT_URL}.wiki.git"
WIKI_REPO_URL="${WIKI_REPO_URL/https\:\/\//https://gitlab-ci-token:${PROJECT_ACCESS_TOKEN}@}"
WIKI_REPO_DIR="${DIST_DIR}/wiki"
DOC_INPUT_DIR="${DIST_DIR}/docs"
DOC_STORAGE_DIR="${WIKI_REPO_DIR}"

pushd "${PROJECT_DIR}"

COMMIT_AUTHOR_NAME=$(git log -1 --format='%aN' ${COMMIT})
COMMIT_AUTHOR_EMAIL=$(git log -1 --format='%aE' ${COMMIT})

popd

echo "Pushing documentation for branch: ${FEATURE_BRANCH_NAME}"

echo "Using wiki repo: ${WIKI_REPO_URL}"

git clone "${WIKI_REPO_URL}" "${WIKI_REPO_DIR}"

pushd "${WIKI_REPO_DIR}"

git config user.name "${COMMIT_AUTHOR_NAME}"
git config user.email "${COMMIT_AUTHOR_EMAIL}"

if [ ${FINAL_MERGE} -eq 1 ]; then
  echo "Attempting to push documentation to the root page"
else
  echo "Attempting to push documentation to the feature branch specific page"
  DOC_STORAGE_DIR="${WIKI_REPO_DIR}/non-stable/${FEATURE_BRANCH_NAME}"
fi

echo "Documentation storage dir: ${DOC_STORAGE_DIR}"

mkdir -vp "${DOC_STORAGE_DIR}"
mkdir -vp "${WIKI_REPO_DIR}/non-stable/"

touch "${WIKI_REPO_DIR}/non-stable/.gitkeep"
git add "${WIKI_REPO_DIR}/non-stable/.gitkeep"

cp -vr "${DOC_INPUT_DIR}"/* "${DOC_STORAGE_DIR}"

git add .

STAGED_FILES=($(git diff --name-only --cached))

if [ ${#STAGED_FILES[@]} -eq 0 ]; then
  echo "No documentation changes to commit... skipping"
else
  git commit -m "Update generated documentation"
  git push origin "HEAD:main"
fi

perform_wiki_cleanup "${WIKI_REPO_DIR}/non-stable" "${PROJECT_URL}"

echo "Documentation url: ${DOC_URL}"

popd
