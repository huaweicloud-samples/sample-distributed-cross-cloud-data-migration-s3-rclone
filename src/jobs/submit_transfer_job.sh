#!/usr/bin/env bash
# 一键提交传输作业：渲染清单 -> 静态核对 -> 创建凭据对象 -> 拉起副本 -> 投递列举作业 -> 跟踪完成 -> 汇总报告。
#
# 前提：infra 已部署完成（集群、命名空间 transfer、存储桶、消息队列、凭据占位）。
# 集群访问方式二选一：已有 KUBECONFIG，或提供 CLUSTER_ID 并用云命令行换取访问凭证。
#
# 必填环境变量（infra/outputs.tf 的 submit_job_command 输出会原样给出这一行）：
#   SRC_BUCKET            源端桶名
#   DST_BUCKET            目的端桶名（必须已改掉占位默认名）
#   ROCKETMQ_NAMESRV      队列接入地址
#   LISTER_IMAGE          列举组件镜像地址
#   WORKER_IMAGE          传输组件镜像地址
# 建议一并给出（省略时按命名前缀 NAME_PREFIX 与区域推导，仍与 infra 输出一致）：
#   PREFIX                源端键前缀，可为空
#   TASK_TOPIC            任务主题名        xfer-rclone-transfer-task
#   CONSUMER_GROUP        消费组名          xfer-rclone-worker-group
#   DEAD_LETTER_TOPIC     业务侧死信主题名  xfer-rclone-transfer-dlq
#   OBS_ENDPOINT          对象存储区域服务端点 obs.<region>.myhuaweicloud.com
# 凭据环境变量（只在本机内存与集群凭据对象里存在，绝不写进仓库文件）：
#   SRC_ENDPOINT  SRC_ACCESS_KEY  SRC_SECRET_KEY
#   HW_ACCESS_KEY HW_SECRET_KEY
#   SWR_USERNAME  SWR_PASSWORD    （私有镜像仓库拉取凭证，形如 <租户名>@<组织名> 与登录指令里的口令）
# 可选覆盖：NAME_PREFIX HW_REGION WORKER_REPLICAS WORKER_PROCESSES_PER_REPLICA BATCH_SIZE
#           RCLONE_CHUNK_SIZE VERIFY_CHECKSUM WORKER_BUFFER_GB RENDER_DIR
#           NAMESPACE CLUSTER_ID TRACK_TIMEOUT
# 渲染产物目录 RENDER_DIR 默认仓库下的 .rendered/，只含名称与参数、不含任何凭据，属本地产物不要提交。
#
# 凭据缺失时本脚本不创建凭据对象，直接使用集群里已存在的对象——
# 绝不用占位内容覆盖运行中的真实凭据。
set -euo pipefail

NAMESPACE="${NAMESPACE:-transfer}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RENDER_DIR="${RENDER_DIR:-${REPO_ROOT}/.rendered}"
JOBS_DIR="${REPO_ROOT}/src/jobs"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=("--kubeconfig" "${KUBECONFIG}")
fi

kubectl_run() { kubectl "${KUBECTL_ARGS[@]}" "$@"; }

step() { printf '\n[%s] %s\n' "$1" "$2"; }
fail() { printf '提交中止：%s\n' "$1" >&2; exit "${2:-1}"; }

is_placeholder() { [[ "$1" =~ ^\<.*\>$ ]]; }

# ---------------------------------------------------------------------------
step "1/8" "前置检查"
command -v kubectl >/dev/null 2>&1 || fail "未找到 kubectl 命令"
command -v python3 >/dev/null 2>&1 || fail "未找到 python3 命令"
for required in SRC_BUCKET DST_BUCKET ROCKETMQ_NAMESRV LISTER_IMAGE WORKER_IMAGE; do
  [[ -n "${!required:-}" ]] || fail "缺少环境变量 ${required}（可从 infra 输出获取，见部署指导）"
done
if [[ -z "${KUBECONFIG:-}" ]] && [[ -n "${CLUSTER_ID:-}" ]] && command -v hw >/dev/null 2>&1; then
  step "1/8" "通过云命令行换取集群访问凭证"
  hw cce get-credentials --cluster-id "${CLUSTER_ID}" --kubeconfig "${REPO_ROOT}/kubeconfig.yaml"
  export KUBECONFIG="${REPO_ROOT}/kubeconfig.yaml"
  KUBECTL_ARGS=("--kubeconfig" "${KUBECONFIG}")
fi
kubectl_run get namespace "${NAMESPACE}" >/dev/null 2>&1 \
  || fail "命名空间 ${NAMESPACE} 不存在，请先完成 infra 部署或执行 kubectl apply -f src/manifests/00-namespace.yaml"

# ---------------------------------------------------------------------------
step "2/8" "渲染作业清单"
python3 "${JOBS_DIR}/render_manifests.py" --out "${RENDER_DIR}" \
  || fail "清单渲染未通过"

# ---------------------------------------------------------------------------
step "3/8" "离线静态核对"
python3 "${JOBS_DIR}/selftest.py" --skip-cloud >/dev/null || fail "协调层自检未通过"
python3 - "${RENDER_DIR}" <<'PY' || fail "渲染产物语法核对未通过"
import glob, sys
try:
    import yaml
except ImportError:
    print("未安装 PyYAML：跳过清单语法核对（渲染器已保证占位符全部注入）")
    raise SystemExit(0)
root = sys.argv[1]
files = sorted(glob.glob(root + "/*.yaml"))
if not files:
    raise SystemExit("渲染目录为空")
for path in files:
    with open(path, encoding="utf-8") as handle:
        docs = [d for d in yaml.safe_load_all(handle) if d]
    if not docs:
        raise SystemExit("清单不含任何对象：" + path)
    for doc in docs:
        for key in ("apiVersion", "kind", "metadata"):
            if key not in doc:
                raise SystemExit("清单 %s 缺少 %s" % (doc.get("kind", "?"), key))
        if not doc["metadata"].get("name"):
            raise SystemExit("清单缺少对象名：" + path)
print("YAML 语法与必备字段核对通过：%d 份清单" % len(files))
PY
python3 "${JOBS_DIR}/check_rclone_conf.py" --file "${RENDER_DIR}/rclone.conf" --mode sample \
  --region "${HW_REGION:-cn-north-4}" || fail "搬运工具配置核对未通过"

# ---------------------------------------------------------------------------
step "4/8" "应用命名空间内的访问模型与运行参数"
kubectl_run apply -f "${RENDER_DIR}/00-namespace.yaml"
kubectl_run apply -f "${RENDER_DIR}/10-rbac.yaml"
kubectl_run apply -f "${RENDER_DIR}/20-transfer-config.yaml"

# ---------------------------------------------------------------------------
step "5/8" "创建凭据对象"
CREDS_READY=true
for cred in SRC_ENDPOINT SRC_ACCESS_KEY SRC_SECRET_KEY HW_ACCESS_KEY HW_SECRET_KEY; do
  if [[ -z "${!cred:-}" ]] || is_placeholder "${!cred:-}"; then
    CREDS_READY=false
    printf '未提供 %s，跳过凭据创建，假定集群中对应凭据对象已写入真实值\n' "${cred}"
  fi
done
if [[ "${CREDS_READY}" == "true" ]]; then
  kubectl_run create secret generic xfer-rclone-source-endpoint -n "${NAMESPACE}" \
    --from-literal=endpoint="${SRC_ENDPOINT}" --dry-run=client -o yaml | kubectl_run apply -f -
  kubectl_run create secret generic xfer-rclone-source-access-key -n "${NAMESPACE}" \
    --from-literal=access_key="${SRC_ACCESS_KEY}" --dry-run=client -o yaml | kubectl_run apply -f -
  kubectl_run create secret generic xfer-rclone-source-secret-key -n "${NAMESPACE}" \
    --from-literal=secret_key="${SRC_SECRET_KEY}" --dry-run=client -o yaml | kubectl_run apply -f -
  kubectl_run create secret generic xfer-rclone-dest-access-key -n "${NAMESPACE}" \
    --from-literal=access_key="${HW_ACCESS_KEY}" --dry-run=client -o yaml | kubectl_run apply -f -
  kubectl_run create secret generic xfer-rclone-dest-secret-key -n "${NAMESPACE}" \
    --from-literal=secret_key="${HW_SECRET_KEY}" --dry-run=client -o yaml | kubectl_run apply -f -
  printf '五个凭据对象已按环境变量写入（值不落盘、不打印）\n'
fi
if [[ -n "${SWR_USERNAME:-}" && -n "${SWR_PASSWORD:-}" ]] && ! is_placeholder "${SWR_PASSWORD:-}"; then
  kubectl_run create secret docker-registry xfer-rclone-swr-pull -n "${NAMESPACE}" \
    --docker-server="${SWR_LOGIN_SERVER:-swr.${HW_REGION:-cn-north-4}.myhuaweicloud.com}" \
    --docker-username="${SWR_USERNAME}" \
    --docker-password="${SWR_PASSWORD}" --dry-run=client -o yaml | kubectl_run apply -f -
  printf '私有镜像仓库拉取凭证已就绪\n'
else
  printf '未提供 SWR_USERNAME/SWR_PASSWORD：沿用集群已有的 xfer-rclone-swr-pull 拉取凭证\n'
fi

# ---------------------------------------------------------------------------
step "6/8" "拉起传输副本并投递列举作业"
kubectl_run apply -f "${RENDER_DIR}/40-worker-deployment.yaml"
kubectl_run rollout status deployment/xfer-rclone-worker -n "${NAMESPACE}" --timeout=300s \
  || fail "传输副本未就绪，先检查镜像是否已推送到私有仓库"
# 同一批对象不重复投递：重跑前先移除上一轮的列举作业
kubectl_run delete job xfer-rclone-lister -n "${NAMESPACE}" --ignore-not-found --wait=true
kubectl_run apply -f "${RENDER_DIR}/30-lister-job.yaml"

# ---------------------------------------------------------------------------
step "7/8" "跟踪完成情况"
echo "列举作业输出对象清单并切批投递，传输副本并行领取。Ctrl-C 可中断跟踪，稍后用 status_transfer_job.sh 继续查看。"
TRACK_TIMEOUT="${TRACK_TIMEOUT:-3600s}"
kubectl_run wait --for=condition=complete "job/xfer-rclone-lister" -n "${NAMESPACE}" --timeout="${TRACK_TIMEOUT}" \
  || echo "列举作业在 ${TRACK_TIMEOUT} 内未完成，可继续用 status_transfer_job.sh 观察"

# ---------------------------------------------------------------------------
step "8/8" "汇总报告"
bash "${JOBS_DIR}/status_transfer_job.sh"

cat <<'MSG'

后续动作：
  * 查看进度：bash src/jobs/status_transfer_job.sh
  * 调大吞吐：修改 infra 的 worker_replicas 后重新执行本脚本（副本数为固定值，不随积压自动增减）
  * 收尾：确认目的端数据完整后删除作业与凭据对象，infra 侧按需销毁资源
MSG
