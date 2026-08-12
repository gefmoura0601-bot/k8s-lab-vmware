#!/usr/bin/env bash
set -Eeuo pipefail
RUNNER_VERSION="${1:?runner version is required}"
RUNNER_SHA256="${2:?runner sha256 is required}"
REPOSITORY_URL="${3:?repository URL is required}"
RUNNER_NAME="${4:-k8s-master}"
RUNNER_LABELS="${5:-master}"
INSTALL_DIR="/home/vagrant/actions-runner"
ARCHIVE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
DOWNLOAD_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${ARCHIVE}"
install -d -o vagrant -g vagrant -m 0755 "${INSTALL_DIR}"
if [[ ! -x "${INSTALL_DIR}/bin/Runner.Listener" ]]; then
  curl -fsSLo "/tmp/${ARCHIVE}" "${DOWNLOAD_URL}"
  echo "${RUNNER_SHA256}  /tmp/${ARCHIVE}" | sha256sum -c -
  tar -xzf "/tmp/${ARCHIVE}" -C "${INSTALL_DIR}"
  rm -f "/tmp/${ARCHIVE}"
  chown -R vagrant:vagrant "${INSTALL_DIR}"
fi
if [[ ! -f "${INSTALL_DIR}/.runner" ]]; then
  if [[ -z "${ACTIONS_RUNNER_TOKEN:-}" ]]; then
    echo "Runner binaries installed; set ACTIONS_RUNNER_TOKEN and reprovision actions-runner to register."
    exit 0
  fi
  runuser -u vagrant -- "${INSTALL_DIR}/config.sh" --unattended --replace \
    --url "${REPOSITORY_URL}" --token "${ACTIONS_RUNNER_TOKEN}" \
    --name "${RUNNER_NAME}" --labels "${RUNNER_LABELS}" --work _work
fi
SERVICE_NAME="$(find /etc/systemd/system -maxdepth 1 -name "actions.runner.*.${RUNNER_NAME}.service" -printf '%f\n' | head -1)"
if [[ -z "${SERVICE_NAME}" ]]; then
  (cd "${INSTALL_DIR}" && ./svc.sh install vagrant)
  SERVICE_NAME="$(find /etc/systemd/system -maxdepth 1 -name "actions.runner.*.${RUNNER_NAME}.service" -printf '%f\n' | head -1)"
fi
[[ -n "${SERVICE_NAME}" ]] || { echo "Runner service was not created" >&2; exit 1; }
systemctl enable --now "${SERVICE_NAME}"
