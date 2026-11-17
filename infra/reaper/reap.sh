#!/usr/bin/env bash
# Delete any project instance older than MAX_AGE_HOURS unless keep=true. The GPU
# pool's control node (tag role=control) gets CONTROL_MAX_AGE_HOURS instead: it
# holds the result files and job logs until `make results-pull`, so it outlives
# the GPU nodes that cost the money (D-1).
# AWS holds the GPU pool, Azure the confidential pool (ADR 010); each pass runs
# only when its cloud is configured.
# Runs hourly from .github/workflows/reaper.yml; DRY_RUN=1 lists only.
set -euo pipefail
MAX_AGE_HOURS=${MAX_AGE_HOURS:-8}
CONTROL_MAX_AGE_HOURS=${CONTROL_MAX_AGE_HOURS:-24}
DRY_RUN=${DRY_RUN:-0}
cutoff=$(date -u -d "-${MAX_AGE_HOURS} hours" +%Y-%m-%dT%H:%M:%SZ)
control_cutoff=$(date -u -d "-${CONTROL_MAX_AGE_HOURS} hours" +%Y-%m-%dT%H:%M:%SZ)
echo "reaping instances created before $cutoff, control nodes before $control_cutoff (dry-run=$DRY_RUN)"

reap_aws() {
  [[ -n "${AWS_REGION:-}" ]] || { echo "aws: AWS_REGION unset, skipping"; return; }
  local not_kept="!(Tags[?Key=='keep' && Value=='true'])"
  local control="Tags[?Key=='role' && Value=='control']"
  aws ec2 describe-instances --region "$AWS_REGION" \
    --filters Name=tag:project,Values=attestland Name=instance-state-name,Values=pending,running,stopping,stopped \
    --query "Reservations[].Instances[?$not_kept && ((!($control) && LaunchTime<'$cutoff') || ($control && LaunchTime<'$control_cutoff'))][].[InstanceId, Tags[?Key=='Name']|[0].Value]" \
    --output text |
  while read -r id name; do
    echo "aws: terminate $name ($id)"
    [[ "$DRY_RUN" = 1 ]] || aws ec2 terminate-instances --region "$AWS_REGION" --instance-ids "$id" >/dev/null
  done
}

reap_azure() {
  [[ -n "${AZURE_SUBSCRIPTION_ID:-}" ]] || { echo "azure: AZURE_SUBSCRIPTION_ID unset, skipping"; return; }
  az vm list --subscription "$AZURE_SUBSCRIPTION_ID" \
    --query "[?tags.project=='attestland' && tags.keep!='true' && timeCreated<'$cutoff'].[id,name]" \
    -o tsv |
  while read -r id name; do
    echo "azure: delete $name"
    [[ "$DRY_RUN" = 1 ]] || az vm delete --ids "$id" --yes --force-deletion true
  done
}

reap_aws
reap_azure
