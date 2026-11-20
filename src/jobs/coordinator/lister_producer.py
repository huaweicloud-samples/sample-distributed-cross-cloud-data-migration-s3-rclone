#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列举组件：分页读取源端对象清单，按批投递任务消息。

一次任务对应一个作业实例，跑完即退出：
  1. 用注入的源端凭据渲染搬运工具配置（只读源端，不需要目的端凭据）；
  2. 递归列举源端桶（可按前缀收窄）；
  3. 每凑满 BATCH_SIZE 个对象键投递一条任务消息，消息体自带源端桶与目的端桶，
     使消费侧无需再查询任务上下文；
  4. 结束打印汇总行，供状态脚本与日志检索使用。

列举与传输共用同一套对象存储工具，不再引入额外的存储接口依赖。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from queue_client import create_producer, producer_group  # noqa: E402
from transfer_common import (  # noqa: E402
    configure_logging,
    env_int,
    env_str,
    list_command,
    valid_bucket,
    write_rclone_conf,
)


def flush_batch(logger: logging.Logger, producer, topic: str, src_bucket: str, dst_bucket: str,
                keys: List[str], seq: int) -> int:
    body = {"source_bucket": src_bucket, "dest_bucket": dst_bucket, "keys": list(keys)}
    message_id = producer.send(topic, body, tags="batch", keys=["%s-%d" % (src_bucket, seq + 1)])
    logger.info("已投递消息 #%d（本批 %d 个对象，标识 %s）", seq + 1, len(keys), message_id or "-")
    return seq + 1


def iter_keys(logger: logging.Logger, command: List[str]):
    """流式产出对象键：清单规模可达亿级，不整体驻留内存。

    列举命令失败时抛出异常，由调用方结束作业。
    """
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    assert process.stdout is not None
    count = 0
    try:
        for line in process.stdout:
            key = line.strip()
            if not key or key.endswith("/"):
                # 目录占位项不是对象，跳过
                continue
            count += 1
            yield key
    finally:
        return_code = process.wait()
        if return_code != 0:
            stderr = (process.stderr.read() if process.stderr else "").strip()
            raise RuntimeError("列举失败：退出码 %d，输出：%s" % (return_code, stderr[:500]))
        logger.info("列举结束，共产出 %d 个对象键", count)


def main() -> int:
    logger = configure_logging("lister")

    src_bucket = env_str("SRC_BUCKET", required=True)
    dst_bucket = env_str("DST_BUCKET", required=True)
    prefix = env_str("PREFIX", "")
    namesrv = env_str("ROCKETMQ_NAMESRV", required=True)
    task_topic = env_str("TASK_TOPIC", required=True)
    consumer_group = env_str("CONSUMER_GROUP", required=True)
    batch_size = max(1, env_int("BATCH_SIZE", 20))

    if not valid_bucket(src_bucket) or not valid_bucket(dst_bucket):
        logger.error("桶名校验不通过：source=%s dest=%s", src_bucket, dst_bucket)
        return 2

    write_rclone_conf(include_dest=False, logger=logger)

    logger.info("开始列举源端对象：bucket=%s prefix=%r 批量=%d", src_bucket, prefix, batch_size)
    total_keys = 0
    total_messages = 0
    batch: List[str] = []

    producer = create_producer(namesrv, producer_group(consumer_group), logger)
    try:
        for key in iter_keys(logger, list_command(src_bucket, prefix)):
            batch.append(key)
            total_keys += 1
            if len(batch) >= batch_size:
                total_messages = flush_batch(logger, producer, task_topic, src_bucket, dst_bucket, batch, total_messages)
                batch = []
            if total_keys and total_keys % 20000 == 0:
                logger.info("已投递 %d 条消息（累计 %d 个对象）", total_messages, total_keys)
        if batch:
            total_messages = flush_batch(logger, producer, task_topic, src_bucket, dst_bucket, batch, total_messages)
            batch = []
    except Exception as exc:  # 列举或投递中断：作业失败，由作业重试机制重来
        logger.error("列举或投递中断：%s", exc)
        return 1
    finally:
        producer.close()

    logger.info(
        "SUMMARY component=lister source_bucket=%s dest_bucket=%s prefix=%r keys_total=%d messages_total=%d",
        src_bucket, dst_bucket, prefix, total_keys, total_messages,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
