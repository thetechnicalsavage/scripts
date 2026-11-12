#!/bin/sh
# v1.0 2026-10-15 - ZDT hook at ep_OfflineAfterUpdate: the shared Oracle home on this node is replaced and the lead
#   domain's servers are about to start; start the other domains' servers on this node again.
exec sh "$(cd "$(dirname "$0")" && pwd)/zdt_other_domains.sh" start
