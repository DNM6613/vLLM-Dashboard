#!/bin/bash
# vllm-dashboard unhealthy watcher (audit 2026-10-20 P2-18).
# Docker --restart only covers the EXITED case; a hung-but-alive main
# process stays unhealthy without being restarted. The container's
# HEALTHCHECK (30s interval, 3 retries) marks it unhealthy first, so this
# restart targets a confirmed dead endpoint, not a transient blip.
# Installed on the production host as /home/uforce/bin/vdb-health-watch.sh
# with a per-minute crontab entry (output -> ~/.vdb-health-watch.log).
set -u
STATUS=$(docker inspect --format '{{.State.Health.Status}}' vllm-dashboard 2>/dev/null || echo "")
if [ "$STATUS" = "unhealthy" ]; then
  if docker restart vllm-dashboard >/dev/null 2>&1; then
    echo "$(date '+%F %T') vllm-dashboard was unhealthy — restarted"
  else
    echo "$(date '+%F %T') vllm-dashboard was unhealthy — restart FAILED"
  fi
fi
