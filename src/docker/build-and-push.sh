#!/usr/bin/env bash
# 构建并推送两个组件镜像到本账号的容器镜像服务（组织与仓库由 infra/swr.tf 创建）。
#
# 镜像地址口径必须与 infra/outputs.tf 的 lister_image / worker_image 完全一致：
#   <仓库登录地址>/<命名前缀即组织名>/<仓库名>:<image_tag>
#   登录地址形如 swr.<region>.myhuaweicloud.com，仓库名为 swr_lister_image / swr_worker_image。
# 仓库保持私有，集群侧靠拉取凭证访问（提交脚本会创建 xfer-rclone-swr-pull）。
#
# 必填环境变量（凭据只从环境读取，绝不写进任何文件）：
#   SWR_USERNAME   登录账号，形如 <租户名>@<组织名>
#   SWR_PASSWORD   控制台「登录指令」里给出的登录口令
#   HW_ACCESS_KEY / HW_SECRET_KEY   仅在使用云命令行换取登录指令时需要
# 可选：HW_REGION(cn-north-4) NAME_PREFIX(xfer-rclone) IMAGE_TAG(v1)
#       SWR_LISTER_IMAGE(lister) SWR_WORKER_IMAGE(worker)
#       SWR_LOGIN_SERVER BASE_IMAGE RCLONE_VERSION WITH_ROCKETMQ CONTAINER_CLI
#
# 用法：在仓库根目录执行 bash src/docker/build-and-push.sh
set -euo pipefail

REGION="${HW_REGION:-cn-north-4}"
NAME_PREFIX="${NAME_PREFIX:-xfer-rclone}"
IMAGE_TAG="${IMAGE_TAG:-v1}"
LISTER_IMAGE_NAME="${SWR_LISTER_IMAGE:-lister}"
WORKER_IMAGE_NAME="${SWR_WORKER_IMAGE:-worker}"
SWR_LOGIN="${SWR_LOGIN_SERVER:-swr.${REGION}.myhuaweicloud.com}"
CONTAINER_CLI="${CONTAINER_CLI:-docker}"
ORG="${NAME_PREFIX}"

fail() { printf '构建中止：%s\n' "$1" >&2; exit "${2:-1}"; }

command -v "${CONTAINER_CLI}" >/dev/null 2>&1 || fail "未找到 ${CONTAINER_CLI} 命令，可用 CONTAINER_CLI=podman 覆盖"
[[ -n "${SWR_USERNAME:-}" ]] || fail "缺少 SWR_USERNAME（形如 <租户名>@<组织名>）"
[[ -n "${SWR_PASSWORD:-}" ]] || fail "缺少 SWR_PASSWORD（控制台「登录指令」给出的口令）"
[[ "${SWR_PASSWORD}" =~ ^\<.*\>$ ]] && fail "SWR_PASSWORD 仍是占位符，请注入真实口令"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

LISTER_REF="${SWR_LOGIN}/${ORG}/${LISTER_IMAGE_NAME}:${IMAGE_TAG}"
WORKER_REF="${SWR_LOGIN}/${ORG}/${WORKER_IMAGE_NAME}:${IMAGE_TAG}"

BUILD_ARGS=(
  --build-arg "BASE_IMAGE=${BASE_IMAGE:-docker.io/library/python:3.12-slim}"
  --build-arg "RCLONE_VERSION=${RCLONE_VERSION:-v1.67.0}"
  --build-arg "WITH_ROCKETMQ=${WITH_ROCKETMQ:-true}"
)

echo "[1/4] 登录镜像仓库 ${SWR_LOGIN}（账号 ${SWR_USERNAME}，口令不回显）"
printf '%s' "${SWR_PASSWORD}" | "${CONTAINER_CLI}" login "${SWR_LOGIN}" \
  --username "${SWR_USERNAME}" --password-stdin

echo "[2/4] 构建列举组件镜像 -> ${LISTER_REF}"
"${CONTAINER_CLI}" build --target lister -f src/docker/Dockerfile "${BUILD_ARGS[@]}" -t "${LISTER_REF}" .

echo "[3/4] 构建传输组件镜像 -> ${WORKER_REF}"
"${CONTAINER_CLI}" build --target worker -f src/docker/Dockerfile "${BUILD_ARGS[@]}" -t "${WORKER_REF}" .

echo "[4/4] 推送两个镜像"
"${CONTAINER_CLI}" push "${LISTER_REF}"
"${CONTAINER_CLI}" push "${WORKER_REF}"

cat <<MSG

两个镜像已推送：
  LISTER_IMAGE='${LISTER_REF}'
  WORKER_IMAGE='${WORKER_REF}'

把上面两个值作为提交脚本的环境变量即可（其余取值同样由 infra/outputs.tf 给出）：
  export LISTER_IMAGE='${LISTER_REF}'
  export WORKER_IMAGE='${WORKER_REF}'
仓库为私有：拉取凭证缺失会得到 401 而停在镜像拉取阶段，
提交脚本会用 SWR_USERNAME/SWR_PASSWORD 创建 xfer-rclone-swr-pull 拉取凭证。
MSG
