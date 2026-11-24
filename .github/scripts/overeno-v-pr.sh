#!/usr/bin/env bash
# Prošel už tento obsah kontrolou v pull requestu?
#
# Použití: overeno-v-pr.sh <soubor workflow, např. tests.yml>
# Vypíše "true", když existuje úspěšný běh daného workflow u pull requestu, jehož commit má stejný
# strom souborů jako právě kontrolovaný commit. Pak není důvod po sloučení do main kontrolovat totéž
# znovu (šetří minuty GitHub Actions). Při jakékoli nejistotě – chyba rozhraní, žádný takový běh –
# vypíše "false" a kontrola proběhne celá.
#
# Potřebuje: GH_TOKEN s oprávněním actions: read a contents: read, proměnnou GITHUB_REPOSITORY.
set -u

workflow="${1:?chybí název souboru workflow}"
tree="$(git rev-parse 'HEAD^{tree}' 2>/dev/null)" || { echo false; exit 0; }

commits="$(gh api "repos/$GITHUB_REPOSITORY/actions/workflows/$workflow/runs?event=pull_request&status=success&per_page=20" \
  --jq '.workflow_runs[].head_sha' 2>/dev/null | sort -u)" || commits=""

for commit in $commits; do
  other="$(gh api "repos/$GITHUB_REPOSITORY/git/commits/$commit" --jq '.tree.sha' 2>/dev/null)" || continue
  if [ "$other" = "$tree" ]; then
    echo true
    exit 0
  fi
done
echo false
