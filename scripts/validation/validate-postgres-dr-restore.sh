#!/usr/bin/env bash
set -Eeuo pipefail

BUNDLE="${1:?uso: validate-postgres-dr-restore.sh <bundle.enc>}"
PASSPHRASE="${DR_BACKUP_PASSPHRASE:-}"
NAMESPACE="${DR_NAMESPACE:-dr-restore-validation}"
WORK_DIR="$(mktemp -d)"

cleanup() {
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  find "${WORK_DIR}" -type f -exec shred -u {} + 2>/dev/null || true
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT
fail() { echo "ERRO: $*" >&2; exit 1; }

[[ -r "${BUNDLE}" ]] || fail "bundle não encontrado"
[[ ${#PASSPHRASE} -ge 20 ]] || fail "DR_BACKUP_PASSPHRASE inválida"
chmod 0700 "${WORK_DIR}"

openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:DR_BACKUP_PASSPHRASE -in "${BUNDLE}" | tar -C "${WORK_DIR}" -xzf -
(cd "${WORK_DIR}" && sha256sum -c SHA256SUMS)
kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true >/dev/null 2>&1 || true
kubectl create namespace "${NAMESPACE}"
kubectl label namespace "${NAMESPACE}" team=platform environment=lab purpose=dr-validation --overwrite

jq --arg ns "${NAMESPACE}" '{apiVersion:"v1",kind:"Secret",metadata:{name:"postgres-restore-secret",namespace:$ns},type:"Opaque",data:.data}' "${WORK_DIR}/postgres-secret.json" | kubectl apply -f -
kubectl -n "${NAMESPACE}" apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: postgres-restore
  labels:
    app: postgres-restore
    team: platform
    environment: lab
spec:
  restartPolicy: Never
  containers:
    - name: postgres
      image: docker.io/library/postgres:16-alpine
      env:
        - name: POSTGRES_DB
          value: appdb
        - name: POSTGRES_USER
          valueFrom:
            secretKeyRef: {name: postgres-restore-secret, key: POSTGRES_USER}
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef: {name: postgres-restore-secret, key: POSTGRES_PASSWORD}
      resources:
        requests: {cpu: 50m, memory: 128Mi}
        limits: {cpu: 500m, memory: 512Mi}

      volumeMounts:
        - {name: data, mountPath: /var/lib/postgresql/data}
  volumes:
    - name: data
      emptyDir: {}
EOF

kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod/postgres-restore --timeout=180s
for _ in $(seq 1 60); do
  # Variáveis expandidas dentro do pod restaurado.
  # shellcheck disable=SC2016
  kubectl -n "${NAMESPACE}" exec postgres-restore -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1 && break
  sleep 2
done
# Variáveis expandidas dentro do pod restaurado.
# shellcheck disable=SC2016
kubectl -n "${NAMESPACE}" exec postgres-restore -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null
# shellcheck disable=SC2016
kubectl -n "${NAMESPACE}" exec -i postgres-restore -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --no-privileges' <"${WORK_DIR}/postgres.dump"
# Variáveis expandidas dentro do pod restaurado.
# shellcheck disable=SC2016
kubectl -n "${NAMESPACE}" exec postgres-restore -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only --inserts --no-owner --no-privileges' >"${WORK_DIR}/restored-data.sql"

EXPECTED="$(cat "${WORK_DIR}/postgres-data.sha256")"
ACTUAL="$(sed -e '/^--/d' -e '/^\\restrict /d' -e '/^\\unrestrict /d' -e '/^[[:space:]]*$/d' "${WORK_DIR}/restored-data.sql" | sha256sum | awk '{print $1}')"
[[ "${ACTUAL}" == "${EXPECTED}" ]] || fail "hash lógico dos dados restaurados diverge"

# shellcheck disable=SC2016
TABLES="$(kubectl -n "${NAMESPACE}" exec postgres-restore -- sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select count(*) from pg_stat_user_tables"')"
echo "RESTORE_STATUS=success"
echo "RESTORE_NAMESPACE=${NAMESPACE}"
echo "RESTORE_TABLES=${TABLES}"
echo "RESTORE_DATA_SHA256=${ACTUAL}"
