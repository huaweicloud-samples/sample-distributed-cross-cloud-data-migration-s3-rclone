#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""作业清单渲染器：把区域、桶名、端点、副本数与分片参数注入 src/manifests 模板。

取值优先级：环境变量 > 与 infra/terraform.tfvars.example 一致的默认值。
命名口径（主题、消费组、凭据对象、镜像地址、存储桶端点）全部按 infra 声明拼装，
不在本脚本里改名；缺项或桶名仍为占位默认值时直接失败，避免提交出一套跑不通的清单。

用法：
    python3 src/jobs/render_manifests.py --out .rendered
    python3 src/jobs/render_manifests.py --check-only     # 只做校验，不写文件
产出的 .rendered/rclone.conf 只含占位凭据，用于离线静态核对。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_DIR = os.path.join(REPO_ROOT, "src", "manifests")
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "jobs", "coordinator"))

from transfer_common import build_rclone_conf, obs_endpoint  # noqa: E402

# 目的端桶名的占位值与 infra/variables.tf 的 dest_bucket_name 默认值一致：
# 该默认名在账号内极可能已被占用，必须改成自己的唯一名称后再提交。
PLACEHOLDER_DEST_BUCKET = "xfer-rclone-dest-please-rename"

TOKEN_PATTERN = re.compile(r"__[A-Z0-9_]+__")


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _bool(name: str, default: str) -> str:
    raw = _env(name, default)
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return "true"
    if normalized in ("0", "false", "no", "off"):
        return "false"
    raise SystemExit("%s 只能取布尔值，当前为 %r" % (name, raw))


def build_values() -> Dict[str, str]:
    """拼装所有模板取值；凭据不进这里（只以凭据对象名引用）。"""
    region = _env("HW_REGION", "cn-north-4")
    name_prefix = _env("NAME_PREFIX", "xfer-rclone")
    swr_org = _env("SWR_ORG", name_prefix)
    swr_login = _env("SWR_LOGIN_SERVER", "swr.%s.myhuaweicloud.com" % region)
    image_tag = _env("IMAGE_TAG", "v1")
    consumer_group = _env("CONSUMER_GROUP", "%s-worker-group" % name_prefix)

    values: Dict[str, str] = {
        "HW_REGION": region,
        "OBS_ENDPOINT": _env("OBS_ENDPOINT", obs_endpoint(region)),
        "SRC_BUCKET": _env("SRC_BUCKET", ""),
        "DST_BUCKET": _env("DST_BUCKET", ""),
        "PREFIX": _env("PREFIX", ""),
        "ROCKETMQ_NAMESRV": _env("ROCKETMQ_NAMESRV", ""),
        "TASK_TOPIC": _env("TASK_TOPIC", "%s-transfer-task" % name_prefix),
        "CONSUMER_GROUP": consumer_group,
        "DEAD_LETTER_TOPIC": _env("DEAD_LETTER_TOPIC", "%s-transfer-dlq" % name_prefix),
        "NATIVE_DEAD_LETTER_TOPIC": _env("NATIVE_DEAD_LETTER_TOPIC", "%%DLQ%%%s" % consumer_group),
        "RETRY_MAX_TIMES": _env("RETRY_MAX_TIMES", "2"),
        "WORKER_REPLICAS": _env("WORKER_REPLICAS", "1"),
        "WORKER_PROCESSES_PER_REPLICA": _env("WORKER_PROCESSES_PER_REPLICA", "6"),
        "BATCH_SIZE": _env("BATCH_SIZE", "20"),
        "RCLONE_CHUNK_SIZE": _env("RCLONE_CHUNK_SIZE", "200M"),
        "RCLONE_UPLOAD_CONCURRENCY": _env("RCLONE_UPLOAD_CONCURRENCY", "4"),
        "RCLONE_BUFFER_SIZE": _env("RCLONE_BUFFER_SIZE", "64M"),
        "RCLONE_BWLIMIT": _env("RCLONE_BWLIMIT", "0"),
        "RCLONE_RETRIES": _env("RCLONE_RETRIES", "3"),
        "RCLONE_RETRY_SLEEP": _env("RCLONE_RETRY_SLEEP", "1s"),
        "VERIFY_CHECKSUM": _bool("VERIFY_CHECKSUM", "false"),
        # 缓冲容量口径与 infra 的 worker_node_data_volume_size（默认 100 GB）一致
        "WORKER_BUFFER_LIMIT": _env("WORKER_BUFFER_GB", "100") + "Gi",
        "LISTER_IMAGE": "%s/%s/%s:%s" % (swr_login, swr_org, _env("SWR_LISTER_IMAGE", "lister"), image_tag),
        "WORKER_IMAGE": "%s/%s/%s:%s" % (swr_login, swr_org, _env("SWR_WORKER_IMAGE", "worker"), image_tag),
        # 传输节点规格 r7.xlarge.4 = 4 vCPU / 32 GiB，单副本独占一台节点
        "WORKER_CPU_REQUEST": _env("WORKER_CPU_REQUEST", "2"),
        "WORKER_MEMORY_REQUEST": _env("WORKER_MEMORY_REQUEST", "16Gi"),
        "WORKER_CPU_LIMIT": _env("WORKER_CPU_LIMIT", "3500m"),
        "WORKER_MEMORY_LIMIT": _env("WORKER_MEMORY_LIMIT", "28Gi"),
    }
    return values


def validate(values: Dict[str, str]) -> List[str]:
    """渲染前的硬性核对；返回问题清单，非空即拒绝提交。"""
    problems: List[str] = []
    for key in ("SRC_BUCKET", "DST_BUCKET", "ROCKETMQ_NAMESRV"):
        if not values[key]:
            problems.append("缺少必需参数 %s（由 infra 输出或提交环境提供）" % key)
    if values["DST_BUCKET"] == PLACEHOLDER_DEST_BUCKET:
        problems.append(
            "目的端桶名仍是占位默认值 %s；桶名全域唯一，请先修改 infra 的 dest_bucket_name "
            "并重新部署，再以新的 DST_BUCKET 提交作业" % PLACEHOLDER_DEST_BUCKET
        )
    if int(values["WORKER_REPLICAS"]) < 1:
        problems.append("WORKER_REPLICAS 至少为 1（本实践为固定副本数，不随积压自动增减）")
    if not values["OBS_ENDPOINT"].startswith("obs.") or not values["OBS_ENDPOINT"].endswith(".myhuaweicloud.com"):
        problems.append("目的端存储端点必须是区域服务端点 obs.<region>.myhuaweicloud.com")
    return problems


def render_template(text: str, values: Dict[str, str]) -> Tuple[str, List[str]]:
    def _sub(match: "re.Match") -> str:
        token = match.group(0)
        key = token.strip("_")
        if key in values:
            return str(values[key])
        return token

    rendered = TOKEN_PATTERN.sub(_sub, text)
    leftovers = sorted(set(TOKEN_PATTERN.findall(rendered)))
    return rendered, leftovers


def main(argv: List[str] = ()) -> int:
    parser = argparse.ArgumentParser(description="渲染传输作业清单")
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, ".rendered"), help="渲染输出目录")
    parser.add_argument("--check-only", action="store_true", help="只校验模板与取值，不写文件")
    args = parser.parse_args(list(argv) or None)

    values = build_values()
    problems = validate(values)
    if problems:
        for item in problems:
            print("参数校验不通过：%s" % item, file=sys.stderr)
        return 2

    if not args.check_only:
        os.makedirs(args.out, exist_ok=True)

    # 渲染前先离线产出一份仅含占位凭据的配置，供静态核对三项访问参数
    sample_env_backup: Dict[str, str] = {}
    sample_keys = {
        "HW_REGION": values["HW_REGION"],
        "OBS_ENDPOINT": values["OBS_ENDPOINT"],
        "SRC_ENDPOINT": "<SRC_ENDPOINT>",
        "SRC_ACCESS_KEY": "<AK>",
        "SRC_SECRET_KEY": "<SK>",
        "DST_ACCESS_KEY": "<HW_ACCESS_KEY>",
        "DST_SECRET_KEY": "<HW_SECRET_KEY>",
    }
    for key, value in sample_keys.items():
        sample_env_backup[key] = os.environ.get(key, "")
        os.environ[key] = value
    try:
        sample_conf = build_rclone_conf(include_dest=True)
    finally:
        for key, value in sample_env_backup.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    failures: List[str] = []
    rendered_files: List[str] = []
    for name in sorted(os.listdir(TEMPLATE_DIR)):
        if not name.endswith(".yaml"):
            continue
        path = os.path.join(TEMPLATE_DIR, name)
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        rendered, leftovers = render_template(text, values)
        if leftovers:
            failures.append("%s 存在未注入的占位符：%s" % (name, "、".join(leftovers)))
            continue
        rendered_files.append(name)
        if not args.check_only:
            target = os.path.join(args.out, name)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)

    if failures:
        for item in failures:
            print("渲染失败：%s" % item, file=sys.stderr)
        return 3

    if not args.check_only:
        conf_path = os.path.join(args.out, "rclone.conf")
        with open(conf_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(sample_conf)
        print("已渲染 %d 份清单到 %s" % (len(rendered_files), args.out))
        print("离线核对用的搬运工具配置：%s（凭据为占位符，不含真实值）" % conf_path)
    else:
        print("模板校验通过：%d 份清单的占位符全部可注入" % len(rendered_files))

    for key in ("HW_REGION", "OBS_ENDPOINT", "TASK_TOPIC", "CONSUMER_GROUP", "WORKER_REPLICAS",
                "WORKER_PROCESSES_PER_REPLICA", "BATCH_SIZE", "RCLONE_CHUNK_SIZE", "LISTER_IMAGE",
                "WORKER_IMAGE"):
        print("  %-28s = %s" % (key, values[key]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
