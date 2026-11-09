package adapters

import (
	"context"
	"encoding/json"
	"strings"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"

	"attestland/healthd/internal/health"
)

const (
	TaintKey   = "fleet.attestland/unhealthy"
	StateLabel = "fleet.attestland/state"
)

// Kubernetes cordons a node and adds the fleet.attestland/unhealthy=<rule>
// taint: NoSchedule for warnings, NoExecute for critical faults so the taint
// manager evicts running pods (spec 1.3).
type Kubernetes struct {
	Client kubernetes.Interface
}

func (k *Kubernetes) Name() string { return "kubernetes" }

// Drain is idempotent: re-running it leaves exactly one healthd taint.
func (k *Kubernetes) Drain(ctx context.Context, node, reason string, mode health.DrainMode) error {
	n, err := k.Client.CoreV1().Nodes().Get(ctx, node, metav1.GetOptions{})
	if err != nil {
		return err
	}
	effect := corev1.TaintEffectNoSchedule
	if mode == health.DrainNow {
		effect = corev1.TaintEffectNoExecute
	}
	rule := strings.TrimPrefix(reason, "healthd:")
	taints := make([]corev1.Taint, 0, len(n.Spec.Taints)+1)
	for _, t := range n.Spec.Taints {
		if t.Key == TaintKey {
			// Keep the stronger effect if one is already present.
			if t.Effect == corev1.TaintEffectNoExecute {
				effect = corev1.TaintEffectNoExecute
			}
			continue
		}
		taints = append(taints, t)
	}
	taints = append(taints, corev1.Taint{Key: TaintKey, Value: rule, Effect: effect})
	patch := map[string]any{
		"metadata": map[string]any{
			"labels":      map[string]string{StateLabel: "Draining"},
			"annotations": map[string]string{"fleet.attestland/drain-reason": reason},
		},
		"spec": map[string]any{"unschedulable": true, "taints": taints},
	}
	b, _ := json.Marshal(patch)
	_, err = k.Client.CoreV1().Nodes().Patch(ctx, node, types.StrategicMergePatchType, b, metav1.PatchOptions{})
	return err
}

// Undrain removes the taint and uncordons (burn-in hand-back).
func (k *Kubernetes) Undrain(ctx context.Context, node string) error {
	n, err := k.Client.CoreV1().Nodes().Get(ctx, node, metav1.GetOptions{})
	if err != nil {
		return err
	}
	taints := make([]corev1.Taint, 0, len(n.Spec.Taints))
	for _, t := range n.Spec.Taints {
		if t.Key != TaintKey {
			taints = append(taints, t)
		}
	}
	patch := map[string]any{"spec": map[string]any{"unschedulable": false, "taints": taints}}
	b, _ := json.Marshal(patch)
	_, err = k.Client.CoreV1().Nodes().Patch(ctx, node, types.StrategicMergePatchType, b, metav1.PatchOptions{})
	return err
}

// State reports drained when the node is cordoned with a healthd taint.
func (k *Kubernetes) State(ctx context.Context, node string) (health.NodeState, error) {
	n, err := k.Client.CoreV1().Nodes().Get(ctx, node, metav1.GetOptions{})
	if apierrors.IsNotFound(err) {
		return health.NodeState{Exists: false}, nil
	}
	if err != nil {
		return health.NodeState{}, err
	}
	st := health.NodeState{Exists: true}
	for _, t := range n.Spec.Taints {
		if t.Key == TaintKey {
			st.Reason = "healthd:" + t.Value
			st.Drained = n.Spec.Unschedulable
		}
	}
	return st, nil
}

// GPUFleetSize counts nodes advertising GPUs (budget denominator).
func GPUFleetSize(c kubernetes.Interface) func(ctx context.Context) int {
	return func(ctx context.Context) int {
		l, err := c.CoreV1().Nodes().List(ctx, metav1.ListOptions{LabelSelector: "nvidia.com/gpu.present=true"})
		if err != nil {
			return 0
		}
		return len(l.Items)
	}
}
