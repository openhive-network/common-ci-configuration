#!/usr/bin/env bash
# The step functions are called indirectly, through `step`.
# shellcheck disable=SC2317,SC2329
# The checks AIDEV's verification slots run (.aidev/project.yaml), as one junit
# report per suite: each named step is a test case, its log the failure body.
#
#   .aidev/run-checks.sh <suite> <step>...
#
#   - shellcheck  ShellCheck on scripts/**/*.sh with sh's glob (scripts/*/*.sh), as
#                 .gitlab-ci.yml's lint_bash_scripts runs it, plus .aidev/'s scripts
#   - yamllint    yamllint on templates/ with the repository's .yamllint, as
#                 lint_ci_templates runs it (warnings don't fail)
#   - pylint      pylint on scripts/python/*.py, as lint_python_scripts runs it,
#                 and misc/pylint2junit.py on its json2 output (pylint.xml)
#   - tests       pytest on tests/ (one junit case per test in tests.xml); a
#                 skipped case when the repository has no tests/ yet
#
# Reports go to test-results/<suite>/: junit.xml (one case per step) plus tests.xml
# and pylint.xml.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
root="$PWD"

suite="${1:?usage: $0 <suite> <step>...}"; shift
out="$root/test-results/$suite"
rm -rf "$out"; mkdir -p "$out"
cases="$out/cases.tsv"; : > "$cases"

status=0
# record CASES_FILE NAME RC SECONDS LOG
record() {
    if [ "$3" -eq 0 ]; then
        printf 'case\t%s\tpass\t%s\t\n' "$2" "$4" >> "$1"
    else
        printf 'case\t%s\tfail\t%s\texit %s\t%s\n' "$2" "$4" "$3" "$5" >> "$1"
    fi
}

step() {
    local name="$1"; shift
    local log="$out/$name.log" t0=$SECONDS rc=0
    echo "== $name" >&2
    "$@" > "$log" 2>&1 < /dev/null || rc=$?
    [ "$rc" -eq 0 ] || { status=1; tail -40 "$log" >&2; }
    record "$cases" "$name" "$rc" "$((SECONDS - t0))" "$log"
}

run_shellcheck() {
    shellcheck --version | sed -n 2p
    local rc=0
    # sh, not bash: CI's `scripts/**/*.sh` is expanded by sh, where ** is *.
    sh -c 'shellcheck -f gcc scripts/**/*.sh' || rc=1
    shellcheck -f gcc .aidev/*.sh .aidev/runtime/*.sh || rc=1
    return "$rc"
}

run_yamllint() {
    yamllint --version
    yamllint --format parsable templates/
}

run_pylint() {
    pylint --version
    local rc=0
    pylint --output-format=text,json2:"$out/pylint-result.json" scripts/python/*.py || rc=$?
    python3 misc/pylint2junit.py "$out/pylint-result.json" "$out/pylint.xml" scripts/python/*.py || rc=1
    return "$rc"
}

run_tests() {
    python3 -m pytest -p no:cacheprovider --junitxml="$out/tests.xml" -o junit_suite_name=tests tests/
}

for s in "$@"; do
    case "$s" in
        shellcheck) step shellcheck run_shellcheck ;;
        yamllint) step yamllint run_yamllint ;;
        # py-compile, the step pylint replaced, is what an approved slot binding
        # predating project.yaml's pylint names.
        pylint|py-compile) step pylint run_pylint ;;
        tests)
            if [ -d tests ]; then
                step tests run_tests
            else
                printf 'case\ttests\tskip\t0\tno tests/ directory yet\n' >> "$cases"
            fi
            ;;
        *) echo "unknown step: $s" >&2; exit 2 ;;
    esac
done
python3 .aidev/junit_cases.py "$out/junit.xml" "$suite" "$cases"
exit "$status"
