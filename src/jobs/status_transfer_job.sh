#!/usr/bin/env bash
# 传输进度查看：作业与副本状态、每副本进度标注、指标行汇总、失败线索。
#
# 数据来源三条，互相印证：
#   1. 列举作业的 SUMMARY 行——本轮共切出多少批；
#   2. 每个副本 Pod 的进度标注——已确认批次、成功对象数、待重试批次；
#   3. 标准输出里的指标行——由日志服务同样采集，控制台也能按同一格式检索。
#
# 队列积压与死信数量在消息队列控制台按主题与消费组查看；
# 本脚本最后一节会从生效中的配置映射读出任务主题、业务侧死信主题、消费组、
# 原生死信主题与重投上限并打印出来，直接照抄到控制台即可
# （原生死信主题由队列在消息达到消费组重投上限后投递）。
#
# 用法：bash src/jobs/status_transfer_job.sh [--watch]
set -euo pipefail

NAMESPACE="${NAMESPACE:-transfer}"
KUBECTL_ARGS=()
if [[ -n "${KUBECONFIG:-}" ]]; then
  KUBECTL_ARGS+=("--kubeconfig" "${KUBECONFIG}")
fi
kubectl_run() { kubectl "${KUBECTL_ARGS[@]}" "$@"; }

section() { printf '\n=== %s ===\n' "$1"; }

# 队列侧参数取自生效中的配置映射，直接给出控制台里要点的主题与消费组，
# 避免按名称规则手拼时和实际渲染结果对不上。
queue_hint() {
  section "队列与死信查看线索"
  local values
  if ! values="$(kubectl_run get configmap xfer-rclone-transfer-config -n "${NAMESPACE}" \
      -o jsonpath='{.data.TASK_TOPIC}{"\t"}{.data.DEAD_LETTER_TOPIC}{"\t"}{.data.CONSUMER_GROUP}{"\t"}{.data.NATIVE_DEAD_LETTER_TOPIC}{"\t"}{.data.RETRY_MAX_TIMES}' 2>/dev/null)"; then
    echo "配置映射尚未创建，先在消息队列控制台按实例查看主题与消费组"
    return 0
  fi
  IFS=$'\t' read -r task_topic dead_letter_topic consumer_group native_dead_letter retry_max <<<"${values}"
  echo "任务主题        ${task_topic}   积压量与消费延迟在队列实例页查看"
  echo "业务侧死信主题  ${dead_letter_topic}   这里只收结构非法、已确认丢弃的消息"
  echo "消费组          ${consumer_group}   重投上限 ${retry_max}"
  echo "原生死信主题    ${native_dead_letter}   超过重投上限后由队列投递，需人工处理"
}

show_once() {
  section "列举作业"
  kubectl_run get job xfer-rclone-lister -n "${NAMESPACE}" -o wide || echo "列举作业尚未创建"
  kubectl_run logs job/xfer-rclone-lister -n "${NAMESPACE}" --tail=5 2>/dev/null \
    | grep -E 'SUMMARY|已投递' | tail -3 || true

  section "传输副本"
  kubectl_run get deployment xfer-rclone-worker -n "${NAMESPACE}" || echo "传输副本尚未创建"
  kubectl_run get pods -n "${NAMESPACE}" -l app.kubernetes.io/component=worker \
    -o custom-columns='POD:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,NODE:.spec.nodeName,AGE:.metadata.creationTimestamp' \
    || true

  section "每副本进度标注"
  kubectl_run get pods -n "${NAMESPACE}" -l app.kubernetes.io/component=worker -o json \
    | python3 -c '
import json, sys
payload = json.load(sys.stdin)
key = "transfer.xfer-rclone/progress"
totals = {"batches_done": 0, "objects_ok": 0, "objects_failed": 0, "batches_retrying": 0}
rows = []
for item in payload.get("items", []):
    annotations = item.get("metadata", {}).get("annotations", {}) or {}
    raw = annotations.get(key)
    name = item["metadata"]["name"]
    if not raw:
        rows.append((name, "尚无进度（副本刚启动或进度回写被拒）"))
        continue
    try:
        data = json.loads(raw)
    except ValueError:
        rows.append((name, "进度标注无法解析"))
        continue
    for field in totals:
        totals[field] += int(data.get(field, 0) or 0)
    rows.append((name, "已确认批次 %s / 成功对象 %s / 失败对象 %s / 待重试批次 %s" % (
        data.get("batches_done", 0), data.get("objects_ok", 0),
        data.get("objects_failed", 0), data.get("batches_retrying", 0))))
for name, text in rows:
    print("%-42s %s" % (name, text))
print()
print("合计：已确认批次 %d，成功对象 %d，失败对象 %d，待重试批次 %d" % (
    totals["batches_done"], totals["objects_ok"], totals["objects_failed"], totals["batches_retrying"]))
' || echo "读取进度标注失败，可回退到日志查看"

  section "指标行汇总（近 200 行日志）"
  kubectl_run logs deployment/xfer-rclone-worker -n "${NAMESPACE}" --tail=200 --all-containers=true 2>/dev/null \
    | grep -oE 'METRIC name=[a-z_]+ value=[0-9.]+' | sort | uniq -c | tail -20 || true

  section "最近失败线索"
  kubectl_run logs deployment/xfer-rclone-worker -n "${NAMESPACE}" --tail=200 --all-containers=true 2>/dev/null \
    | grep -E 'ERROR|WARNING' | tail -5 || echo "近期无错误或告警日志"

  queue_hint
}

if [[ "${1:-}" == "--watch" ]]; then
  INTERVAL="${WATCH_INTERVAL:-30}"
  echo "每 ${INTERVAL}s 刷新一次，Ctrl-C 退出"
  while :; do
    clear || true
    date '+%F %T'
    show_once
    sleep "${INTERVAL}"
  done
else
  show_once
fi
