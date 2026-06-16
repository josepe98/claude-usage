# claude-usage Helm chart

A Helm chart for running the [claude-usage](https://github.com/josepe98/claude-usage) dashboard on Kubernetes.

## Prerequisites

- Kubernetes 1.23+
- Helm 3.8+
- A pre-built container image of claude-usage. The chart defaults to
  `ghcr.io/jakduch/claude-usage:<chart appVersion>`, but you can point it at any
  image that exposes `python cli.py serve` and includes the project code at the
  working directory (see the sibling `Dockerfile` PR or build your own).

## Quick start

```bash
helm install claude-usage ./deploy/helm/claude-usage

# Reach the dashboard
kubectl port-forward svc/claude-usage 8090:8090
open http://127.0.0.1:8090
```

## Upgrading

```bash
helm upgrade claude-usage ./deploy/helm/claude-usage \
  --set image.tag=v0.2.0
```

## Common value overrides

### Custom image

```bash
helm install claude-usage ./deploy/helm/claude-usage \
  --set image.repository=my-registry.example.com/claude-usage \
  --set image.tag=2026-05-28 \
  --set image.pullPolicy=Always
```

### Enable the ingress

```yaml
# my-values.yaml
ingress:
  enabled: true
  className: nginx
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-prod
  hosts:
    - host: claude-usage.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: claude-usage-tls
      hosts:
        - claude-usage.example.com
```

```bash
helm install claude-usage ./deploy/helm/claude-usage -f my-values.yaml
```

### Mount an existing `~/.claude` directory

The simplest path is to use an existing PVC that you have already populated:

```bash
helm install claude-usage ./deploy/helm/claude-usage \
  --set persistence.existingClaim=claude-data-shared
```

For NFS-backed sharing, create a PV/PVC pair first and then reference it:

```yaml
# nfs-pv.yaml
apiVersion: v1
kind: PersistentVolume
metadata:
  name: claude-data
spec:
  capacity:
    storage: 5Gi
  accessModes: [ReadWriteMany]
  nfs:
    server: nfs.example.com
    path: /exports/claude
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: claude-data
spec:
  accessModes: [ReadWriteMany]
  resources:
    requests:
      storage: 5Gi
  volumeName: claude-data
```

```bash
kubectl apply -f nfs-pv.yaml
helm install claude-usage ./deploy/helm/claude-usage \
  --set persistence.existingClaim=claude-data
```

For local development you can also bind-mount a host path via the cluster's
default `hostPath` provisioner - works with k3s, kind, minikube:

```yaml
# values-hostpath.yaml
persistence:
  enabled: true
  storageClass: standard   # or whichever hostPath StorageClass your cluster ships
  size: 1Gi
```

### Disable persistence (ephemeral pod)

```bash
helm install claude-usage ./deploy/helm/claude-usage \
  --set persistence.enabled=false
```

## Populating the database

The dashboard reads `~/.claude/usage.db`, which is built from the JSONL
transcripts under `~/.claude/projects/`. There are two common ways to populate
the volume:

### Option A: copy transcripts in, then run scan

```bash
# Copy your local transcripts into the pod's persistent volume
POD=$(kubectl get pod -l app.kubernetes.io/name=claude-usage -o jsonpath='{.items[0].metadata.name}')
kubectl cp ~/.claude/projects $POD:/home/claude/.claude/projects

# Rebuild the DB from inside the pod
kubectl exec deploy/claude-usage -- python cli.py scan
```

### Option B: one-shot Job

Useful for scheduled rebuilds. Save as `scan-job.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: claude-usage-scan
spec:
  ttlSecondsAfterFinished: 600
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: scan
          image: ghcr.io/jakduch/claude-usage:0.1.0
          command: ["python", "cli.py", "scan"]
          env:
            - name: HOME
              value: /home/claude
          volumeMounts:
            - name: claude-data
              mountPath: /home/claude/.claude
      volumes:
        - name: claude-data
          persistentVolumeClaim:
            claimName: claude-usage-data  # or your existingClaim
```

```bash
kubectl apply -f scan-job.yaml
```

You can also wrap it in a `CronJob` for nightly rebuilds.

### Option C: sidecar

Add a sidecar to the Deployment via `values.yaml`-style overrides (requires a
small post-render or a kustomize layer; not built into the chart by default to
keep the templates small). For most setups the one-shot Job is cleaner.

## Image build

The chart does not build an image. Either:

1. Use the published image at `ghcr.io/jakduch/claude-usage:<tag>` (default).
2. Build your own from the sibling [`Dockerfile`](../../Dockerfile) PR and push
   it to your own registry, then override `image.repository` / `image.tag`.

A minimal local build:

```bash
docker build -t my-registry.example.com/claude-usage:dev .
docker push my-registry.example.com/claude-usage:dev
helm install claude-usage ./deploy/helm/claude-usage \
  --set image.repository=my-registry.example.com/claude-usage \
  --set image.tag=dev
```

## Uninstall

```bash
helm uninstall claude-usage
# PVCs are not deleted by default. Drop them manually if desired:
kubectl delete pvc claude-usage-data
```
