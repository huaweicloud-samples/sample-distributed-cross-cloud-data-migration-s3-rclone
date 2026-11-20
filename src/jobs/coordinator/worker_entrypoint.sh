#!/usr/bin/env bash
# 传输副本容器的入口脚本。
#
# 做两件事：
#   1. 启动时按注入的凭据渲染一次搬运工具配置（渲染失败直接退出让副本重启，
#      避免带病消费）；配置写入内存卷，权限由渲染脚本收紧。
#   2. 拉起 WORKER_PROCESSES_PER_REPLICA 个消费进程，各自以同一消费组领取任务消息，
#      进程异常退出后按固定退避重启；副本收到停止信号时一并结束所有子进程，
#      并把优雅终止窗口留给在途对象（见清单的 terminationGracePeriodSeconds）。
#
# 端到端吞吐上限 = 副本数 × 本进程数 × 出网带宽，三项都是固定值，
# 需要更高吞吐时调大 infra 的 worker_replicas 后重新执行提交脚本。
set -euo pipefail

COORDINATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 静态核对脚本的位置：镜像内为 /opt/transfer/jobs，仓库内为 src/jobs（即本目录的上一级）。
if [[ -f "${COORDINATOR_DIR}/../jobs/check_rclone_conf.py" ]]; then
  JOBS_DIR="$(cd "${COORDINATOR_DIR}/../jobs" && pwd)"
else
  JOBS_DIR="$(cd "${COORDINATOR_DIR}/.." && pwd)"
fi
PROCESSES="${WORKER_PROCESSES_PER_REPLICA:-6}"
RESTART_BACKOFF_SECONDS="${RESTART_BACKOFF_SECONDS:-5}"

if ! [[ "${PROCESSES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "WORKER_PROCESSES_PER_REPLICA 必须是正整数，当前为 '${PROCESSES}'" >&2
  exit 2
fi

# 1. 渲染配置（导出给所有子进程复用）
RCLONE_CONFIG="$(python3 "${COORDINATOR_DIR}/render_rclone_conf.py" --print-path)"
export RCLONE_CONFIG
export RCLONE_CONF_READY=true

# 启动前静态核对三项访问参数与凭据字段是否齐全，不通过就不消费，避免整批写失败
python3 "${JOBS_DIR}/check_rclone_conf.py" --file "${RCLONE_CONFIG}" --mode runtime --region "${HW_REGION:-cn-north-4}"

echo "副本启动：配置 ${RCLONE_CONFIG}，并行消费进程 ${PROCESSES} 个"

CHILD_PIDS=()

shutdown() {
  echo "收到停止信号，结束 ${#CHILD_PIDS[@]} 个消费进程"
  for pid in "${CHILD_PIDS[@]}"; do
    kill -TERM "${pid}" 2>/dev/null || true
  done
  wait || true
  exit 0
}

trap shutdown SIGTERM SIGINT

# 2. 每个进程独立消费循环，异常退出后退避重启
run_worker() {
  local index="$1"
  local code=0
  while :; do
    code=0
    WORKER_PROCESS_INDEX="${index}" python3 -u "${COORDINATOR_DIR}/worker_consumer.py" || code=$?
    echo "消费进程 ${index} 退出（码 ${code}），${RESTART_BACKOFF_SECONDS}s 后重启"
    sleep "${RESTART_BACKOFF_SECONDS}"
  done
}

for ((i = 1; i <= PROCESSES; i++)); do
  run_worker "${i}" &
  CHILD_PIDS+=("$!")
done

wait
