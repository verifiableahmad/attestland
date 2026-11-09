#!/usr/bin/env bash
# Delete any project GPU instance older than MAX_AGE_HOURS unless keep=true.
# Everything runs on GCP.
# Runs hourly from .github/workflows/reaper.yml; DRY_RUN=1 lists only.
set -euo pipefail
MAX_AGE_HOURS=${MAX_AGE_HOURS:-8}
DRY_RUN=${DRY_RUN:-0}
cutoff=$(date -u -d "-${MAX_AGE_HOURS} hours" +%Y-%m-%dT%H:%M:%SZ)
echo "reaping instances created before $cutoff (dry-run=$DRY_RUN)"

reap_gcp() {
  [[ -n "${GCP_PROJECT:-}" ]] || { echo "gcp: GCP_PROJECT unset, skipping"; return; }
  gcloud compute instances list --project "$GCP_PROJECT" \
    --filter="labels.project=attestland AND NOT labels.keep=true AND creationTimestamp<'$cutoff'" \
    --format="value(name,zone.basename())" |
  while read -r name zone; do
    echo "gcp: delete $name ($zone)"
    [[ "$DRY_RUN" = 1 ]] || gcloud compute instances delete "$name" --zone "$zone" \
      --project "$GCP_PROJECT" --quiet --delete-disks=all
  done
}

reap_gcp
