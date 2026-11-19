#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务队列接入层：分布式消息服务 RocketMQ 版的生产与消费。

实例、主题与消费组由 infra/rocketmq.tf 创建，本模块只按名称接入：
  * 任务主题 TASK_TOPIC              —— 消息体为「源端桶 / 目的端桶 / 一批对象键」
  * 业务侧死信主题 DEAD_LETTER_TOPIC —— 结构非法、无法交给传输流程处理的消息
  * 原生死信主题 %DLQ%<消费组名>      —— 达到消费组重投上限后由队列自动投递
  * 重投上限由消费组的 retry_max_times 决定（infra 变量同名），客户端不再自行计数

队列实例仅在专有网络内接入、不开公网，也不启用访问认证，因此本模块不携带任何队列凭据。

QUEUE_BACKEND 取值：
  * rocketmq（默认）—— 走开源客户端，需要镜像内已装原生运行库；
  * file            —— 本地文件队列，仅用于无网络的离线自检，不连接任何远端服务。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Callable, Dict, Iterable, List, Optional

CONSUME_SUCCESS = "SUCCESS"
RECONSUME_LATER = "RECONSUME_LATER"

PRODUCER_GROUP_SUFFIX = "-producer"


class TaskMessage:
    """与队列实现无关的任务消息。"""

    def __init__(self, body: Dict, message_id: str = "", tags: str = ""):
        self.body = body
        self.message_id = message_id or str(uuid.uuid4())
        self.tags = tags

    def json(self) -> str:
        return json.dumps(self.body, ensure_ascii=False, separators=(",", ":"))


class QueueError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# RocketMQ 实现
# ---------------------------------------------------------------------------

class RocketMQProducer:
    """任务消息生产者。同一进程内复用一个实例，退出时关闭。"""

    def __init__(self, namesrv: str, group: str, logger: Optional[logging.Logger] = None):
        try:
            from rocketmq.client import Producer  # 由镜像内的原生运行库提供
        except ImportError as exc:  # pragma: no cover - 依赖运行期环境
            raise QueueError(
                "无法加载消息队列客户端：%s。请确认镜像由 src/docker/Dockerfile 构建，"
                "且原生运行库已随镜像安装。" % exc
            ) from exc
        self._logger = logger or logging.getLogger("queue")
        self._producer = Producer(group)
        self._producer.set_name_server_address(namesrv)
        self._producer.start()
        self._logger.info("生产者已接入，消费组标识：%s", group)

    def send(self, topic: str, body: Dict, tags: str = "", keys: Iterable[str] = ()) -> str:
        from rocketmq.client import Message

        msg = Message(topic)
        msg.set_body(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
        if tags:
            msg.set_tags(tags)
        key_list = [k for k in keys if k]
        if key_list:
            msg.set_keys(" ".join(key_list[:3]))
        result = self._producer.send_sync(msg)
        return getattr(result, "msg_id", "") or ""

    def close(self) -> None:
        try:
            self._producer.shutdown()
        except Exception:  # pragma: no cover - 关闭失败不影响已投递的消息
            self._logger.warning("生产者关闭异常，忽略")


class RocketMQConsumer:
    """任务消息消费者：集群消费模式，同组副本分摊队列分片。"""

    def __init__(
        self,
        namesrv: str,
        group: str,
        topic: str,
        handler: Callable[[TaskMessage], str],
        logger: Optional[logging.Logger] = None,
        thread_count: int = 1,
    ):
        try:
            from rocketmq.client import ConsumeStatus, PushConsumer  # noqa: WPS433
        except ImportError as exc:  # pragma: no cover
            raise QueueError(
                "无法加载消息队列客户端：%s。请确认镜像由 src/docker/Dockerfile 构建。" % exc
            ) from exc
        self._logger = logger or logging.getLogger("queue")
        self._status = ConsumeStatus
        self._handler = handler
        self._topic = topic
        self._consumer = PushConsumer(group)
        self._consumer.set_name_server_address(namesrv)
        if thread_count > 1 and hasattr(self._consumer, "set_thread_count"):
            self._consumer.set_thread_count(thread_count)
        self._consumer.subscribe(topic, self._callback, "*")
        self._thread_count = thread_count

    def _callback(self, message) -> object:
        try:
            body = json.loads(message.body.decode("utf-8", errors="replace"))
        except (ValueError, AttributeError):
            body = {}
        status = self._handler(TaskMessage(body, message_id=getattr(message, "id", "") or ""))
        if status == CONSUME_SUCCESS:
            return self._status.CONSUME_SUCCESS
        return self._status.RECONSUME_LATER

    def start(self) -> None:
        self._consumer.start()
        self._logger.info("消费者已接入主题 %s，进程内消费线程 %d", self._topic, self._thread_count)

    def shutdown(self) -> None:
        try:
            self._consumer.shutdown()
        except Exception:  # pragma: no cover
            self._logger.warning("消费者关闭异常，忽略")


# ---------------------------------------------------------------------------
# 本地文件队列（离线自检用，不触网）
# ---------------------------------------------------------------------------

class FileProducer:
    def __init__(self, namesrv: str, group: str, logger: Optional[logging.Logger] = None):
        self._path = os.environ.get("FAKE_QUEUE_PATH", "fake-queue.jsonl")
        self._lock = threading.Lock()
        self._logger = logger or logging.getLogger("queue")

    def send(self, topic: str, body: Dict, tags: str = "", keys: Iterable[str] = ()) -> str:
        record = {"topic": topic, "body": body}
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return "local"

    def close(self) -> None:
        return None


class FileConsumer:
    def __init__(
        self,
        namesrv: str,
        group: str,
        topic: str,
        handler: Callable[[TaskMessage], str],
        logger: Optional[logging.Logger] = None,
        thread_count: int = 1,
    ):
        self._path = os.environ.get("FAKE_QUEUE_PATH", "fake-queue.jsonl")
        self._topic = topic
        self._handler = handler
        self._logger = logger or logging.getLogger("queue")
        self._stop = threading.Event()

    def start(self) -> None:
        records: List[Dict] = []
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    item = json.loads(line)
                    if item.get("topic") == self._topic:
                        records.append(item)
        self._logger.info("离线队列读取到 %d 条主题 %s 的消息", len(records), self._topic)
        for item in records:
            self._handler(TaskMessage(item["body"], message_id="local"))
        self._logger.info("离线队列消费完成（自检模式，不保持长轮询）")

    def shutdown(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def _backend() -> str:
    return (os.environ.get("QUEUE_BACKEND") or "rocketmq").strip().lower()


def create_producer(namesrv: str, group: str, logger: Optional[logging.Logger] = None):
    if _backend() == "file":
        return FileProducer(namesrv, group, logger)
    return RocketMQProducer(namesrv, group, logger)


def create_consumer(
    namesrv: str,
    group: str,
    topic: str,
    handler: Callable[[TaskMessage], str],
    logger: Optional[logging.Logger] = None,
    thread_count: int = 1,
):
    if _backend() == "file":
        return FileConsumer(namesrv, group, topic, handler, logger, thread_count)
    return RocketMQConsumer(namesrv, group, topic, handler, logger, thread_count)


def producer_group(consumer_group: str) -> str:
    """生产组标识由消费组名派生，保持与 infra 命名前缀一致。"""
    return (consumer_group or "xfer-rclone") + PRODUCER_GROUP_SUFFIX
