#!/usr/bin/env bash
# v1.0 2026-10-15 - builds the ZDT extension jar for common Oracle homes in the layout the ZDT guide requires
#   (resources/extensionConfiguration.json, scripts/unix/*.sh). Copy the jar, extensionProperties.json and the
#   other_domains.csv settings to the same paths on every node (03_stage_on_nodes.sh does this when HOOK_JAR is set).
# Usage: ./build_hook_jar.sh [output.jar]          (default ./zdt_shared_home_hooks.jar)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(realpath -m "${1:-$HERE/zdt_shared_home_hooks.jar}")
[[ ! -e "$OUT" ]] || { echo "exists: $OUT" >&2; exit 1; }
command -v zip > /dev/null || { echo "zip not found (a jar is a zip archive)" >&2; exit 1; }
cd "$HERE/src"
zip -q -X -r "$OUT" resources scripts
chmod 600 "$OUT"
unzip -l "$OUT"
