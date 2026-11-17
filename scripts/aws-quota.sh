#!/usr/bin/env bash
# aws-quota.sh <pool dir>: fail before `tofu apply` if the on-demand G and VT
# vCPU quota cannot hold the GPU nodes the pool asks for (OPS-3). An apply that
# runs out half-way leaves the pool half built.
set -euo pipefail
dir=${1:?pool dir, e.g. infra/gpu-pool}
var() {  # the value tofu will use: terraform.tfvars, else the variable's default
  local v
  v=$(sed -n "s/^ *$1 *= *\"\{0,1\}\([^\" #]*\).*/\1/p" "$dir/terraform.tfvars" 2>/dev/null | tail -1)
  [[ -n $v ]] || v=$(awk -v n="$1" '$0 ~ "variable \""n"\"" {f=1} f && /default/ {gsub(/[" ]/, "", $3); print $3; exit}' "$dir/variables.tf")
  echo "$v"
}
region=$(var region) type=$(var gpu_instance_type) nodes=$(var gpu_nodes)
vcpus=$(aws ec2 describe-instance-types --region "$region" --instance-types "$type" \
  --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text)
# L-DB2E81BA: Running On-Demand G and VT instances (vCPUs). Reading it needs
# servicequotas:GetServiceQuota (runbook: ServiceQuotasReadOnlyAccess on al-operator).
if ! quota=$(aws service-quotas get-service-quota --region "$region" --service-code ec2 --quota-code L-DB2E81BA \
    --query 'Quota.Value' --output text 2>&1); then
  echo "warning: cannot read the G/VT quota, not checked: ${quota##*: }" >&2
  exit 0
fi
quota=${quota%.*}
# vCPUs of G/VT instances already running in the region that are not this pool's
running=$(aws ec2 describe-instances --region "$region" \
  --filters Name=instance-state-name,Values=pending,running Name=instance-type,Values='g*','vt*' \
  --query 'Reservations[].Instances[?!(Tags[?Key==`project` && Value==`attestland`])][].CpuOptions.[CoreCount,ThreadsPerCore]' \
  --output text | awk '{s += $1 * $2} END {print s + 0}')
need=$(( nodes * vcpus ))
echo "G/VT on-demand quota in $region: $quota vCPU; pool needs $need ($nodes x $type), others use $running"
if (( need + running > quota )); then
  echo "not enough G/VT quota: lower gpu_nodes or gpu_instance_type in $dir/terraform.tfvars, or raise the quota" >&2
  exit 1
fi
