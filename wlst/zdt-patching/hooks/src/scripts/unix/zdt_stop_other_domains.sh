#!/bin/sh
# v1.0 2026-10-15 - ZDT hook at ep_OfflineBeforeUpdate: the lead domain's servers on this node are down and the shared
#   Oracle home is about to be replaced; stop the other domains' servers that run from it on this node.
exec sh "$(cd "$(dirname "$0")" && pwd)/zdt_other_domains.sh" stop
