#!/bin/bash
set -e

# Deploy documentation to GitHub Pages repository
#
# This is a unified script that handles multiple deployment modes:
# - simple: Single docs directory (e.g., retype output for manual docs)
# - ts-wiki: TypeScript API docs + wiki/extra docs
# - full: TypeScript + Python + wiki/extra docs
#
# Usage: deploy_github_pages.sh --mode <mode> --version <version> --project <project> \
#                               --github-repo <repo> --github-token <token> \
#                               --docs-subdir <subdir> [mode-specific options]
#
# Common options:
#   --mode              Deployment mode: simple, ts-wiki, or full
#   --version           Version name (e.g., "develop", "v1.0.0", branch slug)
#   --project           Project subdirectory in hive-doc (e.g., "wax", "workerbee")
#   --github-repo       GitHub repository (e.g., "openhive-network/hive-doc")
#   --github-token      GitHub token with repo write access
#   --docs-subdir       Target subdirectory name (e.g., "manual", "wiki")
#   --source-url        Source URL for commit message (e.g., "https://gitlab.syncad.com/hive/wax")
#
# Mode-specific options:
#   simple mode:
#     --docs-dir        Directory containing documentation to deploy
#
#   ts-wiki mode:
#     --ts-docs-dir     Directory containing TypeScript HTML docs
#     --extra-docs-dir  Directory containing additional docs (e.g., wiki markdown)
#
#   full mode:
#     --ts-docs-dir     Directory containing TypeScript HTML docs
#     --py-docs-dir     Directory containing Python mkdocs output
#     --extra-docs-dir  Directory containing additional docs (e.g., wiki markdown)

SCRIPTPATH="$( cd -- "$(dirname "$0")" >/dev/null 2>&1 ; pwd -P )"

# Parse arguments
MODE=""
VERSION=""
PROJECT_SUBDIR=""
GITHUB_REPO=""
GITHUB_TOKEN=""
GITHUB_DOCS_SUBDIR=""
SOURCE_URL=""
DOCS_DIR=""
TS_DOCS_DIR=""
PY_DOCS_DIR=""
EXTRA_DOCS_DIR=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --mode)
      MODE="$2"
      shift 2
      ;;
    --version)
      VERSION="$2"
      shift 2
      ;;
    --project)
      PROJECT_SUBDIR="$2"
      shift 2
      ;;
    --github-repo)
      GITHUB_REPO="$2"
      shift 2
      ;;
    --github-token)
      GITHUB_TOKEN="$2"
      shift 2
      ;;
    --docs-subdir)
      GITHUB_DOCS_SUBDIR="$2"
      shift 2
      ;;
    --source-url)
      SOURCE_URL="$2"
      shift 2
      ;;
    --docs-dir)
      DOCS_DIR="$2"
      shift 2
      ;;
    --ts-docs-dir)
      TS_DOCS_DIR="$2"
      shift 2
      ;;
    --py-docs-dir)
      PY_DOCS_DIR="$2"
      shift 2
      ;;
    --extra-docs-dir)
      EXTRA_DOCS_DIR="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Validate required arguments
: "${MODE:?Missing --mode argument}"
: "${VERSION:?Missing --version argument}"
: "${PROJECT_SUBDIR:?Missing --project argument}"
: "${GITHUB_REPO:?Missing --github-repo argument}"
: "${GITHUB_TOKEN:?Missing --github-token argument}"
: "${GITHUB_DOCS_SUBDIR:?Missing --docs-subdir argument}"

# Validate mode-specific arguments
case "${MODE}" in
  simple)
    : "${DOCS_DIR:?Missing --docs-dir argument for simple mode}"
    ;;
  ts-wiki)
    : "${TS_DOCS_DIR:?Missing --ts-docs-dir argument for ts-wiki mode}"
    : "${EXTRA_DOCS_DIR:?Missing --extra-docs-dir argument for ts-wiki mode}"
    ;;
  full)
    : "${TS_DOCS_DIR:?Missing --ts-docs-dir argument for full mode}"
    : "${PY_DOCS_DIR:?Missing --py-docs-dir argument for full mode}"
    : "${EXTRA_DOCS_DIR:?Missing --extra-docs-dir argument for full mode}"
    ;;
  *)
    echo "Unknown mode: ${MODE}. Valid modes: simple, ts-wiki, full"
    exit 1
    ;;
esac

WORK_DIR=$(mktemp -d)
GITHUB_PAGES_BRANCH="main"

cleanup() {
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

echo "=== Deploying ${PROJECT_SUBDIR} docs version ${VERSION} to ${GITHUB_REPO} (mode: ${MODE}) ==="

# Clone the GitHub Pages repository
cd "${WORK_DIR}"
git clone --depth 1 --branch "${GITHUB_PAGES_BRANCH}" \
  "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git" repo 2>/dev/null || {
  echo "Branch ${GITHUB_PAGES_BRANCH} doesn't exist, creating new repository structure"
  mkdir repo
  cd repo
  git init
  git checkout -b "${GITHUB_PAGES_BRANCH}"
  cd ..
}

cd repo

# Create project directory structure based on mode
DOCS_BASE="${PROJECT_SUBDIR}/${VERSION}/${GITHUB_DOCS_SUBDIR}"

case "${MODE}" in
  simple)
    mkdir -p "${DOCS_BASE}"
    echo "Copying docs from ${DOCS_DIR} to ${DOCS_BASE}/"
    cp -r "${DOCS_DIR}/." "${DOCS_BASE}/"
    ;;
  ts-wiki)
    mkdir -p "${DOCS_BASE}/ts"
    echo "Copying extra docs from ${EXTRA_DOCS_DIR} to ${DOCS_BASE}/"
    cp -r "${EXTRA_DOCS_DIR}/." "${DOCS_BASE}/"
    echo "Copying TypeScript docs from ${TS_DOCS_DIR} to ${DOCS_BASE}/ts/"
    cp -r "${TS_DOCS_DIR}/." "${DOCS_BASE}/ts/"
    ;;
  full)
    mkdir -p "${DOCS_BASE}/ts"
    mkdir -p "${DOCS_BASE}/python"
    echo "Copying extra docs from ${EXTRA_DOCS_DIR} to ${DOCS_BASE}/"
    cp -r "${EXTRA_DOCS_DIR}/." "${DOCS_BASE}/"
    echo "Copying TypeScript docs from ${TS_DOCS_DIR} to ${DOCS_BASE}/ts/"
    cp -r "${TS_DOCS_DIR}/." "${DOCS_BASE}/ts/"
    echo "Copying Python docs from ${PY_DOCS_DIR} to ${DOCS_BASE}/python/"
    cp -r "${PY_DOCS_DIR}/." "${DOCS_BASE}/python/"
    ;;
esac

# Clean up any unexpected directories in version folder (only keep manual and wiki)
VERSION_PATH="${PROJECT_SUBDIR}/${VERSION}"
for d in "${VERSION_PATH}"/*/; do
  if [ -d "$d" ]; then
    dir_name=$(basename "$d")
    if [ "$dir_name" != "manual" ] && [ "$dir_name" != "wiki" ]; then
      echo "Removing unexpected directory: ${d}"
      rm -rf "$d"
    fi
  fi
done

# Generate directory listing index page
generate_index_page() {
  local dir="$1"
  local title="$2"
  shift 2
  local subdirs=("$@")

  cat > "${dir}/index.html" << 'HEADER'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg-color: #0d1117;
      --card-bg: #161b22;
      --text-color: #c9d1d9;
      --text-muted: #8b949e;
      --accent-color: #58a6ff;
      --border-color: #30363d;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg-color: #ffffff;
        --card-bg: #f6f8fa;
        --text-color: #24292f;
        --text-muted: #57606a;
        --accent-color: #0969da;
        --border-color: #d0d7de;
      }
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
      background: var(--bg-color);
      color: var(--text-color);
      line-height: 1.6;
      min-height: 100vh;
    }
    .container { max-width: 700px; margin: 0 auto; padding: 2rem; }
    header {
      text-align: center;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border-color);
    }
    h1 { font-size: 1.8rem; margin-bottom: 0.5rem; }
    .subtitle { color: var(--text-muted); font-size: 1rem; }
    .dir-list { display: flex; flex-direction: column; gap: 0.75rem; }
    .dir-item {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 1rem 1.25rem;
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      text-decoration: none;
      color: var(--text-color);
      transition: border-color 0.2s, background 0.2s;
    }
    .dir-item:hover {
      border-color: var(--accent-color);
      background: var(--bg-color);
    }
    .dir-icon {
      width: 20px;
      height: 20px;
      color: var(--accent-color);
    }
    .dir-name { font-weight: 500; }
    .breadcrumb {
      margin-bottom: 1.5rem;
      color: var(--text-muted);
      font-size: 0.9rem;
    }
    .breadcrumb a { color: var(--accent-color); text-decoration: none; }
    .breadcrumb a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>__TITLE__</h1>
      <p class="subtitle">Select a subdirectory</p>
    </header>
    <main>
      <div class="dir-list">
HEADER

  # Replace title placeholder (use | delimiter to handle versions with slashes)
  sed -i "s|__TITLE__|${title}|g" "${dir}/index.html"

  # Add directory links
  local folder_icon='<svg class="dir-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>'

  for subdir in "${subdirs[@]}"; do
    cat >> "${dir}/index.html" << ITEM
        <a href="${subdir}/" class="dir-item">
          ${folder_icon}
          <span class="dir-name">${subdir}/</span>
        </a>
ITEM
  done

  cat >> "${dir}/index.html" << 'FOOTER'
      </div>
    </main>
  </div>
</body>
</html>
FOOTER
}

# Update subdirs.json for this version directory
# Each deployment adds its subdirectory to the list, enabling dynamic index.html
VERSION_DIR="${PROJECT_SUBDIR}/${VERSION}"
SUBDIRS_FILE="${VERSION_DIR}/subdirs.json"
python3 << EOF
import json
from pathlib import Path

subdirs_file = Path("${SUBDIRS_FILE}")
current_subdir = "${GITHUB_DOCS_SUBDIR}"

if subdirs_file.exists():
    data = json.loads(subdirs_file.read_text())
else:
    data = {"subdirs": []}

if current_subdir not in data["subdirs"]:
    data["subdirs"].append(current_subdir)

# Sort: manual first, then wiki, then others alphabetically
def sort_key(s):
    if s == "manual":
        return (0, s)
    elif s == "wiki":
        return (1, s)
    return (2, s)

data["subdirs"] = sorted(data["subdirs"], key=sort_key)
subdirs_file.write_text(json.dumps(data, indent=2))
EOF

echo "Updated subdirs.json:"
cat "${SUBDIRS_FILE}"

# Generate dynamic index.html for version directory that loads subdirs.json
echo "Generating dynamic index page for ${VERSION_DIR}/"
cat > "${VERSION_DIR}/index.html" << 'DYNINDEX'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg-color: #0d1117;
      --card-bg: #161b22;
      --text-color: #c9d1d9;
      --text-muted: #8b949e;
      --accent-color: #58a6ff;
      --border-color: #30363d;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg-color: #ffffff;
        --card-bg: #f6f8fa;
        --text-color: #24292f;
        --text-muted: #57606a;
        --accent-color: #0969da;
        --border-color: #d0d7de;
      }
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
      background: var(--bg-color);
      color: var(--text-color);
      line-height: 1.6;
      min-height: 100vh;
    }
    .container { max-width: 700px; margin: 0 auto; padding: 2rem; }
    header {
      text-align: center;
      margin-bottom: 2rem;
      padding-bottom: 1.5rem;
      border-bottom: 1px solid var(--border-color);
    }
    h1 { font-size: 1.8rem; margin-bottom: 0.5rem; }
    .subtitle { color: var(--text-muted); font-size: 1rem; }
    .dir-list { display: flex; flex-direction: column; gap: 0.75rem; }
    .dir-item {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 1rem 1.25rem;
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 6px;
      text-decoration: none;
      color: var(--text-color);
      transition: border-color 0.2s, background 0.2s;
    }
    .dir-item:hover {
      border-color: var(--accent-color);
      background: var(--bg-color);
    }
    .dir-icon {
      width: 20px;
      height: 20px;
      color: var(--accent-color);
    }
    .dir-name { font-weight: 500; }
    .loading { text-align: center; padding: 2rem; color: var(--text-muted); }
    .error { text-align: center; padding: 2rem; color: #f85149; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>__TITLE__</h1>
      <p class="subtitle">Select a subdirectory</p>
    </header>
    <main>
      <div id="subdirs" class="dir-list">
        <div class="loading">Loading...</div>
      </div>
    </main>
  </div>
  <script>
    const folderIcon = '<svg class="dir-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>';

    fetch('subdirs.json')
      .then(r => r.json())
      .then(data => {
        const container = document.getElementById('subdirs');
        if (!data.subdirs || data.subdirs.length === 0) {
          container.innerHTML = '<div class="error">No subdirectories available</div>';
          return;
        }
        container.innerHTML = data.subdirs.map(s => `
          <a href="${s}/" class="dir-item">
            ${folderIcon}
            <span class="dir-name">${s}/</span>
          </a>
        `).join('');
      })
      .catch(err => {
        document.getElementById('subdirs').innerHTML = '<div class="error">Failed to load subdirectories</div>';
      });
  </script>
</body>
</html>
DYNINDEX

# Replace title placeholder (use | delimiter to handle versions with slashes like bw/branch-name)
sed -i "s|__TITLE__|${PROJECT_SUBDIR} ${VERSION}|g" "${VERSION_DIR}/index.html"

# Generate index.html for docs subdir based on mode
echo "Generating index page for ${DOCS_BASE}/"
case "${MODE}" in
  simple)
    # No subdirectory index needed for simple mode - docs are directly in the subdir
    ;;
  ts-wiki)
    generate_index_page "${DOCS_BASE}" "${PROJECT_SUBDIR} ${VERSION} - ${GITHUB_DOCS_SUBDIR}" "ts"
    ;;
  full)
    generate_index_page "${DOCS_BASE}" "${PROJECT_SUBDIR} ${VERSION} - ${GITHUB_DOCS_SUBDIR}" "ts" "python"
    ;;
esac

# Update versions.json for this project
VERSIONS_FILE="${PROJECT_SUBDIR}/versions.json"
python3 << EOF
import json
import re
from pathlib import Path

versions_file = Path("${VERSIONS_FILE}")
version = "${VERSION}"

def semver_key(v):
    """Sort key: develop first, then semver descending."""
    if v == "develop":
        return (0, [])
    # Extract version numbers, strip leading 'v'
    match = re.match(r'v?(\d+)\.(\d+)\.(\d+)', v)
    if match:
        return (1, [-int(match.group(1)), -int(match.group(2)), -int(match.group(3))])
    return (2, [v])

if versions_file.exists():
    data = json.loads(versions_file.read_text())
else:
    data = {"versions": []}

if version not in data["versions"]:
    data["versions"].append(version)

data["versions"] = sorted(data["versions"], key=semver_key)
versions_file.write_text(json.dumps(data, indent=2))
EOF

echo "Updated versions.json:"
cat "${VERSIONS_FILE}"

# Copy project landing page from template
cp "${SCRIPTPATH}/doc-index-template.html" "${PROJECT_SUBDIR}/index.html"

# Update placeholders in the landing page based on project
sed -i "s/__PROJECT_NAME__/${PROJECT_SUBDIR}/g" "${PROJECT_SUBDIR}/index.html"

# Add .nojekyll to prevent Jekyll processing
touch .nojekyll

# Commit and push
git config user.email "ci@syncad.com"
git config user.name "GitLab CI"

# Extract GitHub org/user from repo for URL
GITHUB_ORG="${GITHUB_REPO%%/*}"
BASE_URL="https://${GITHUB_ORG}.github.io/${GITHUB_REPO#*/}/${PROJECT_SUBDIR}"

# Build commit message
COMMIT_MSG="Deploy ${PROJECT_SUBDIR} docs ${VERSION}

Automated deployment from GitLab CI"
if [ -n "${SOURCE_URL}" ]; then
  COMMIT_MSG="${COMMIT_MSG}
Source: ${SOURCE_URL}"
fi

git add -A
if git diff --staged --quiet; then
  echo "No changes to deploy"
else
  git commit -m "${COMMIT_MSG}"

  git push "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPO}.git" "${GITHUB_PAGES_BRANCH}"
  echo "=== Successfully deployed ${PROJECT_SUBDIR} ${VERSION} ==="
fi

echo "=== Documentation available at: ==="
echo "  Version list: ${BASE_URL}/"
echo "  Version page: ${BASE_URL}/${VERSION}/"
echo "  ${GITHUB_DOCS_SUBDIR^}: ${BASE_URL}/${VERSION}/${GITHUB_DOCS_SUBDIR}/"

case "${MODE}" in
  ts-wiki)
    echo "  TypeScript: ${BASE_URL}/${VERSION}/${GITHUB_DOCS_SUBDIR}/ts/"
    ;;
  full)
    echo "  TypeScript: ${BASE_URL}/${VERSION}/${GITHUB_DOCS_SUBDIR}/ts/"
    echo "  Python: ${BASE_URL}/${VERSION}/${GITHUB_DOCS_SUBDIR}/python/"
    ;;
esac
