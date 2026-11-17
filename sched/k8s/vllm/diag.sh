#!/usr/bin/env bash
# make serve-diag: why vLLM's fetch-weights init container cannot reach the
# models bucket (I-16, first seen 2026-11-16). Run on the control node. Changes
# nothing except one short-lived pod (s3diag, in default, removed on exit).
# Compares the same fetch from the control node, from a pod on the k8s-pool
# node, and the settings the pod is given.
set -uo pipefail
ns=serving
echo "== settings the pod gets (s3-settings)"
kubectl -n $ns get cm s3-settings -o jsonpath='{.data}'; echo
prefix=$(kubectl -n $ns get cm s3-settings -o jsonpath='{.data.S3_BUCKET_PREFIX}')
region=$(kubectl -n $ns get cm s3-settings -o jsonpath='{.data.AWS_REGION}')
model=$(kubectl -n $ns get cm vllm-model -o jsonpath='{.data.MODEL_PREFIX}')
src="s3://${prefix:+$prefix-}$model"
echo "== the init container's last attempt"
kubectl -n $ns logs deploy/vllm -c fetch-weights --tail=20 2>&1
echo "== from the control node (its instance role): $src"
aws s3 ls "$src/" --region "${region:-us-east-1}" 2>&1 | head -5
node=$(kubectl get nodes -l fleet.attestland/pool=k8s -o jsonpath='{.items[0].metadata.name}')
[[ -n $node ]] || { echo "no node in the k8s pool (pool-move one first)"; exit 1; }
echo "== from a pod on $node (pod network, CoreDNS, IMDS through the node)"
image=$(sed -n 's/^AWSCLI_IMAGE=//p' /etc/fleet/versions.env)
# In default, outside the namespaces Kueue manages (manager-config.yaml).
kubectl run s3diag -n default --rm -i --restart=Never --image="$image" \
  --overrides="{\"spec\":{\"nodeName\":\"$node\"}}" --command -- sh -c "
    getent hosts ${prefix:+$prefix-}${model%%/*}.s3.${region:-us-east-1}.amazonaws.com || echo 'dns: no answer'
    getent hosts kubernetes.default.svc.cluster.local || echo 'cluster dns: no answer'
    aws sts get-caller-identity --output text 2>&1
    aws s3 ls '$src/' --region '${region:-us-east-1}' 2>&1 | head -5" 2>&1
echo "== CoreDNS and Calico"
kubectl -n kube-system get pods -o wide -l k8s-app=kube-dns 2>&1
kubectl -n kube-system get pods -o wide -l k8s-app=calico-node 2>&1
