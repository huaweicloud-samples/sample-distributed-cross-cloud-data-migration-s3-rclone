#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协调层离线自检：全程使用假输入与本地文件队列，不连接任何远端服务。

覆盖四件事：
  1. 配置渲染 + 静态核对：端点、寻址风格、签名方式三项，凭据必须是占位符；
  2. 列举切批：按 BATCH_SIZE 切分，消息体自含源端桶与目的端桶；
  3. 副本消费：整批成功走确认，任一批失败走重投；
  4. 非法消息走业务侧死信主题，不进重试通道。

用法：python3 src/jobs/selftest.py [--skip-cloud]（该开关为兼容提交脚本的空操作，本自检永不联网）
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from types import SimpleNamespace
from typing import List

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
# 同一份脚本既能在仓库内直接跑，也能在镜像里跑（镜像内布局为 /opt/transfer/jobs 与
# /opt/transfer/coordinator），因此两种目录关系都加进模块搜索路径。
for _candidate in (
    HERE,
    os.path.join(HERE, "coordinator"),
    os.path.join(REPO_ROOT, "src", "jobs"),
    os.path.join(REPO_ROOT, "src", "jobs", "coordinator"),
):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

import check_rclone_conf  # noqa: E402
from queue_client import CONSUME_SUCCESS, RECONSUME_LATER, TaskMessage, create_consumer, create_producer, producer_group  # noqa: E402
from transfer_common import build_rclone_conf, copy_command, list_command, validate_message, write_rclone_conf  # noqa: E402
import lister_producer  # noqa: E402
import worker_consumer  # noqa: E402

REGION = "cn-north-4"
FAILURES: List[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print("  PASS  %s" % label)
    else:
        print("  FAIL  %s%s" % (label, ("：" + detail) if detail else ""))
        FAILURES.append(label)


def setup_env(workdir: str) -> None:
    os.environ.update(
        {
            "HW_REGION": REGION,
            "OBS_ENDPOINT": "obs.%s.myhuaweicloud.com" % REGION,
            "SRC_ENDPOINT": "<SRC_ENDPOINT>",
            "SRC_ACCESS_KEY": "<AK>",
            "SRC_SECRET_KEY": "<SK>",
            "DST_ACCESS_KEY": "<HW_ACCESS_KEY>",
            "DST_SECRET_KEY": "<HW_SECRET_KEY>",
            "RCLONE_CONFIG_DIR": os.path.join(workdir, "rclone"),
            "RCLONE_CACHE_DIR": os.path.join(workdir, "buffer"),
            "RCLONE_CHUNK_SIZE": "200M",
            "RCLONE_UPLOAD_CONCURRENCY": "4",
            "RCLONE_BUFFER_SIZE": "64M",
            "RCLONE_BWLIMIT": "0",
            "RCLONE_RETRIES": "3",
            "RCLONE_RETRY_SLEEP": "1s",
            "RCLONE_EXTRA_ARGS": "",
            "VERIFY_CHECKSUM": "false",
            "SRC_BUCKET": "source-example-bucket",
            "DST_BUCKET": "target-example-bucket",
            "PREFIX": "data/2024/",
            "BATCH_SIZE": "20",
            "ROCKETMQ_NAMESRV": "127.0.0.1:9876",
            "TASK_TOPIC": "xfer-rclone-transfer-task",
            "CONSUMER_GROUP": "xfer-rclone-worker-group",
            "DEAD_LETTER_TOPIC": "xfer-rclone-transfer-dlq",
            "NATIVE_DEAD_LETTER_TOPIC": "%DLQ%xfer-rclone-worker-group",
            "PROGRESS_REPORT_INTERVAL_SECONDS": "5",
            "FAKE_QUEUE_PATH": os.path.join(workdir, "queue.jsonl"),
            "QUEUE_BACKEND": "file",
            "LOG_LEVEL": "INFO",
            "WORKER_PROCESS_INDEX": "0",
            "POD_NAME": "",
        }
    )


def fake_runner_factory(fail_on_key: str = ""):
    """替换真实搬运工具调用：只记录命令，不执行任何外部程序。"""
    calls: List[List[str]] = []

    def runner(command: List[str], logger) -> SimpleNamespace:
        calls.append(command)
        joined = " ".join(command)
        if fail_on_key and fail_on_key in joined:
            return SimpleNamespace(returncode=42, stderr=b"simulated transfer failure")
        return SimpleNamespace(returncode=0, stderr=b"")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_config() -> None:
    print("[1] 配置渲染与静态核对")
    text = build_rclone_conf(include_dest=True)
    path = write_rclone_conf(include_dest=True)
    mode = "source-only" if "[dest]" not in text else "full"
    check("配置生成在指定目录（%s）" % mode, os.path.isfile(path))
    if os.name == "nt":
        # Windows 不保存 POSIX 权限位，权限断言只在类 POSIX 环境上成立
        check("权限收紧为 0600（本机为 Windows，跳过该断言）", True)
    else:
        check("权限收紧为 0600", oct(os.stat(path).st_mode & 0o777) == "0o600")
    problems = check_rclone_conf.check(path, "sample", REGION)
    check("三项访问参数与占位凭据核对通过", not problems, "；".join(problems))
    os.environ["DST_ACCESS_KEY"] = "<HW_ACCESS_KEY>"
    runtime_problems = check_rclone_conf.check(path, "runtime", REGION)
    check("运行期模式能识别出未注入的占位凭据", bool(runtime_problems))
    bad = os.path.join(os.path.dirname(path), "bad.conf")
    with open(bad, "w", encoding="utf-8") as handle:
        handle.write(
            "[source]\ntype = s3\nprovider = Other\naccess_key_id = <AK>\n"
            "secret_access_key = <SK>\nendpoint = example.com\nforce_path_style = false\n\n"
            "[dest]\ntype = s3\nprovider = Other\nenv_auth = true\naccess_key_id = <AK>\n"
            "secret_access_key = <SK>\nendpoint = obs.us-example.myhuaweicloud.com\n"
            "region = cn-north-4\nforce_path_style = true\n"
        )
    problems = check_rclone_conf.check(bad, "sample", REGION)
    check("路径风格与环境凭证链被核对拒绝", len(problems) >= 3, str(problems))

    # 配置字典里取错值时，渲染阶段直接失败，不把错误参数带进容器运行期
    for key, wrong_value in (("OBS_FORCE_PATH_STYLE", "true"), ("OBS_SIGN_ALGORITHM", "v2")):
        os.environ[key] = wrong_value
        try:
            build_rclone_conf(include_dest=True)
            rejected = False
        except RuntimeError:
            rejected = True
        finally:
            os.environ.pop(key, None)
        check("%s 取错值时渲染直接失败" % key, rejected)


def test_batching() -> None:
    print("[2] 列举切批")
    producer = create_producer(os.environ["ROCKETMQ_NAMESRV"], producer_group(os.environ["CONSUMER_GROUP"]))
    seq = 0
    keys = ["data/2024/part-%03d.bin" % i for i in range(45)]
    batch: List[str] = []
    for key in keys:
        batch.append(key)
        if len(batch) >= int(os.environ["BATCH_SIZE"]):
            seq = lister_producer.flush_batch(
                worker_consumer.logging.getLogger("selftest"), producer, os.environ["TASK_TOPIC"],
                os.environ["SRC_BUCKET"], os.environ["DST_BUCKET"], batch, seq,
            )
            batch = []
    if batch:
        seq = lister_producer.flush_batch(
            worker_consumer.logging.getLogger("selftest"), producer, os.environ["TASK_TOPIC"],
            os.environ["SRC_BUCKET"], os.environ["DST_BUCKET"], batch, seq,
        )
    producer.close()
    check("45 个对象切成 3 批", seq == 3, "实际 %d" % seq)

    with open(os.environ["FAKE_QUEUE_PATH"], "r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    task_records = [r for r in records if r["topic"] == os.environ["TASK_TOPIC"]]
    check("任务主题收到 3 条消息", len(task_records) == 3, str(len(task_records)))
    body = task_records[0]["body"]
    check("消息体自含源端桶、目的端桶与键列表",
          sorted(body.keys()) == ["dest_bucket", "keys", "source_bucket"] and len(body["keys"]) == 20)
    check("消息结构校验放行", validate_message(body) == [])
    check("缺字段消息被拒绝", bool(validate_message({"keys": []})))
    check("桶名不合规消息被拒绝",
          bool(validate_message({"source_bucket": "bad_bucket!", "dest_bucket": "ok-ok", "keys": ["a"]})))


def test_consumption() -> None:
    print("[3] 副本消费与重投")
    worker = worker_consumer.Worker(logger=worker_consumer.logging.getLogger("selftest-worker"),
                                    runner=fake_runner_factory())
    handled: List[list] = []
    consumer = create_consumer(
        worker.namesrv, worker.consumer_group, worker.task_topic,
        lambda message: (handled.append(message.body["keys"]), worker.handle(message))[1],
        worker.logger,
    )
    consumer.start()
    consumer.shutdown()
    check("离线队列 3 批全部确认", worker._counters["batches_done"] == 3, str(worker._counters))
    check("成功对象数为 45", worker._counters["objects_ok"] == 45, str(worker._counters))
    command = worker.runner.calls[-1]  # type: ignore[attr-defined]
    check("复制命令使用分片大小与缓冲区参数",
          "--s3-chunk-size" in command and "200M" in command and "--cache-dir" in command)
    check("复制命令两端使用渲染出的远程名",
          command[-2].startswith("source:") and command[-1].startswith("dest:"))

    retrying = worker_consumer.Worker(
        logger=worker_consumer.logging.getLogger("selftest-retry"),
        runner=fake_runner_factory(fail_on_key="part-005"),
    )
    status = retrying.handle(TaskMessage({"source_bucket": os.environ["SRC_BUCKET"],
                                          "dest_bucket": os.environ["DST_BUCKET"],
                                          "keys": ["data/2024/part-004.bin", "data/2024/part-005.bin"]}))
    check("任一对象失败时整批交回队列重投", status == RECONSUME_LATER, str(status))

    poison = worker_consumer.Worker(
        logger=worker_consumer.logging.getLogger("selftest-poison"),
        runner=fake_runner_factory(),
    )
    status = poison.handle(TaskMessage({"source_bucket": os.environ["SRC_BUCKET"],
                                        "dest_bucket": os.environ["DST_BUCKET"],
                                        "keys": ["data/2024/bad%key.bin"]}))
    check("对象键不合规判整批失败并计入重试", status == RECONSUME_LATER, str(status))

    illegal = worker_consumer.Worker(
        logger=worker_consumer.logging.getLogger("selftest-illegal"),
        runner=fake_runner_factory(),
    )
    status = illegal.handle(TaskMessage({"keys": "not-a-list"}))
    check("结构非法消息直接确认并转发死信", status == CONSUME_SUCCESS, str(status))
    with open(os.environ["FAKE_QUEUE_PATH"], "r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    dlq = [r for r in records if r["topic"] == os.environ["DEAD_LETTER_TOPIC"]]
    check("业务侧死信主题收到 1 条非法消息", len(dlq) == 1, str(len(dlq)))
    check("死信记录带拒绝原因", bool(dlq and dlq[0]["body"].get("reject_reasons")))
    handled.clear()


def test_source_only_config() -> None:
    print("[4] 列举侧只需源端配置，两端路径可拼装")
    path = write_rclone_conf(include_dest=False)
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    check("列举侧配置不含目的端远程", "[dest]" not in text)
    check("列举侧配置源端寻址为虚拟托管", "force_path_style = false" in text)
    check("列举命令递归且只取对象",
          list_command("a-bucket", "data/2024")
          == ["rclone", "lsf", "source:a-bucket/data/2024", "--files-only", "-R"])
    command = copy_command("a-b", "k1", "c-d")
    check("复制命令两端路径带桶名与对象键",
          command[-2] == "source:a-b/k1" and command[-1] == "dest:c-d/k1")


def main(argv: List[str] = ()) -> int:
    workdir = tempfile.mkdtemp(prefix="xfer-selftest-")
    setup_env(workdir)
    try:
        for step in (test_config, test_batching, test_consumption, test_source_only_config):
            step()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if FAILURES:
        print("\n自检未通过：%d 项失败" % len(FAILURES))
        for item in FAILURES:
            print("  - %s" % item)
        return 1
    print("\n自检全部通过：配置三项参数、切批、确认与重投、死信转发均符合预期（全程未连接远端服务）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
