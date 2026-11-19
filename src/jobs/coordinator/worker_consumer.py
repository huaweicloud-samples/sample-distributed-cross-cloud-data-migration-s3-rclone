#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""传输副本：以消费组方式领取任务消息，逐对象把源端数据写入目的端存储桶。

与队列的配合（语义对齐 infra/rocketmq.tf 的声明）：
  * 整批全部成功才确认（CONSUME_SUCCESS）；
  * 任一对象失败则返回 RECONSUME_LATER，由队列按消费组的 retry_max_times 重投整批；
    达到上限后由队列投递到原生死信主题 %DLQ%<消费组名>；
  * 结构非法、无法处理的消息不进重试通道：直接投递到业务侧死信主题并确认，
    避免同一条消息反复占用副本。

搬运参数（分片大小、并发、重试、校验开关）全部来自配置字典；
每个副本进程启动时按注入的凭据渲染一次配置，凭据不落镜像、不落代码。

一个副本 Pod 内并行运行的进程数由 WORKER_PROCESSES_PER_REPLICA 决定，
由 src/jobs/coordinator/worker_entrypoint.sh 拉起；本脚本只负责单个进程。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from progress_report import ProgressReporter  # noqa: E402
from queue_client import CONSUME_SUCCESS, RECONSUME_LATER, TaskMessage, create_consumer, create_producer  # noqa: E402
from transfer_common import (  # noqa: E402
    PROGRESS_ANNOTATION,
    configure_logging,
    copy_command,
    env_bool,
    env_int,
    env_str,
    emit_metric,
    run,
    valid_key,
    validate_message,
    write_rclone_conf,
)

Runner = Callable[[List[str], logging.Logger], object]


class Worker:
    """单进程消费循环。执行器与命令构造可注入，便于离线自检。"""

    def __init__(
        self,
        logger: Optional[logging.Logger] = None,
        runner: Runner = run,
        command_builder: Callable[[str, str, str], List[str]] = copy_command,
    ):
        self.logger = logger or configure_logging("worker")
        self.runner = runner
        self.command_builder = command_builder
        self.namesrv = env_str("ROCKETMQ_NAMESRV", required=True)
        self.task_topic = env_str("TASK_TOPIC", required=True)
        self.consumer_group = env_str("CONSUMER_GROUP", required=True)
        self.dead_letter_topic = env_str("DEAD_LETTER_TOPIC", "")
        self.instance = os.environ.get("POD_NAME") or os.environ.get("HOSTNAME") or "local"
        self.process_index = env_str("WORKER_PROCESS_INDEX", "0")
        self.verify_checksum = env_bool("VERIFY_CHECKSUM", False)

        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {"batches_done": 0, "objects_ok": 0, "objects_failed": 0, "batches_retrying": 0}
        self._progress = ProgressReporter(PROGRESS_ANNOTATION, self.logger)
        self._interval = max(5, env_int("PROGRESS_REPORT_INTERVAL_SECONDS", 30))
        self._last_report = 0.0
        self._dlq_producer = None

    # ------------------------------------------------------------------
    # 队列回调
    # ------------------------------------------------------------------
    def handle(self, message: TaskMessage) -> str:
        started = time.time()
        reasons = validate_message(message.body)
        if reasons:
            self.logger.error("消息结构非法（标识 %s）：%s", message.message_id or "-", "；".join(reasons))
            self._route_to_dead_letter(message, reasons)
            return CONSUME_SUCCESS

        body = message.body
        source_bucket = body["source_bucket"]
        dest_bucket = body["dest_bucket"]
        keys: List[str] = body["keys"]
        self.logger.info(
            "领取批次：进程 %s 共 %d 个对象，%s -> %s", self.process_index, len(keys), source_bucket, dest_bucket
        )

        failed: List[str] = []
        for index, key in enumerate(keys, start=1):
            if not valid_key(key):
                self.logger.error("对象键不通过白名单校验，本批判定失败：%r", key)
                failed.append(key)
                break
            result = self.runner(self.command_builder(source_bucket, key, dest_bucket), self.logger)
            if getattr(result, "returncode", 1) != 0:
                detail = _stderr_text(result)
                self.logger.error("复制失败 [%d/%d] %s：%s", index, len(keys), key, detail[:300])
                failed.append(key)
                break
            with self._lock:
                self._counters["objects_ok"] += 1

        elapsed = time.time() - started
        with self._lock:
            if failed:
                self._counters["objects_failed"] += len(failed)
                self._counters["batches_retrying"] += 1
            else:
                self._counters["batches_done"] += 1
        emit_metric(
            self.logger,
            "batch_duration_seconds",
            round(elapsed, 1),
            component="worker",
            instance=self.instance,
            process=self.process_index,
        )
        emit_metric(
            self.logger,
            "objects_transferred" if not failed else "objects_failed",
            len(keys) if not failed else len(failed),
            component="worker",
            instance=self.instance,
            process=self.process_index,
        )
        self.maybe_report(force=bool(failed))

        if failed:
            self.logger.warning("本批 %d 个对象未完成，交回队列按消费组重投上限重试", len(failed))
            return RECONSUME_LATER
        self.logger.info("本批 %d 个对象已完成，用时 %.1fs", len(keys), elapsed)
        return CONSUME_SUCCESS

    # ------------------------------------------------------------------
    def _route_to_dead_letter(self, message: TaskMessage, reasons: List[str]) -> None:
        if not self.dead_letter_topic:
            self.logger.warning("未配置业务侧死信主题，非法消息只记录不转发")
            return
        try:
            if self._dlq_producer is None:
                self._dlq_producer = create_producer(self.namesrv, "%s-dlq-producer" % self.consumer_group, self.logger)
            payload = dict(message.body) if isinstance(message.body, dict) else {"raw": str(message.body)}
            payload["reject_reasons"] = reasons
            payload["rejected_by"] = "%s/%s" % (self.instance, self.process_index)
            self._dlq_producer.send(self.dead_letter_topic, payload, tags="rejected")
            self.logger.info("非法消息已转发到业务侧死信主题 %s", self.dead_letter_topic)
        except Exception as exc:
            self.logger.error("转发非法消息失败：%s", exc)

    def maybe_report(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_report < self._interval:
            return
        self._last_report = now
        with self._lock:
            payload = dict(self._counters)
        payload["updated_at"] = int(now)
        payload["process"] = self.process_index
        payload["verify_checksum"] = self.verify_checksum
        self._progress.report(payload)

    def close(self) -> None:
        if self._dlq_producer is not None:
            self._dlq_producer.close()
            self._dlq_producer = None
        self.maybe_report(force=True)
        with self._lock:
            self.logger.info(
                "SUMMARY component=worker instance=%s process=%s batches_done=%d objects_ok=%d objects_failed=%d",
                self.instance,
                self.process_index,
                self._counters["batches_done"],
                self._counters["objects_ok"],
                self._counters["objects_failed"],
            )


def _stderr_text(result) -> str:
    data = getattr(result, "stderr", b"") or b""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def main() -> int:
    logger = configure_logging("worker")
    # 副本内并行进程由入口脚本统一拉起，配置只渲染一次；直接单独运行本脚本时自行渲染
    if os.environ.get("RCLONE_CONF_READY") != "true":
        write_rclone_conf(include_dest=True, logger=logger)

    worker = Worker(logger=logger)
    stop = threading.Event()

    def _on_signal(signum, _frame):
        logger.info("收到退出信号 %d，停止领取新批次并等待在途对象完成", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    consumer = create_consumer(
        worker.namesrv,
        worker.consumer_group,
        worker.task_topic,
        worker.handle,
        logger,
        thread_count=1,
    )
    consumer.start()
    try:
        while not stop.is_set():
            stop.wait(1)
    finally:
        consumer.shutdown()
        worker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
