#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""传输作业协调层公共部分：运行参数读取、搬运工具配置渲染、输入校验、指标输出。

设计要点：
  * 对象存储访问一律走兼容 S3 协议的接口，参数由本模块集中渲染，避免各处分散配置：
    目的端固定为区域服务端点 + 虚拟托管寻址 + V4 签名，不使用路径风格，也不使用低版本签名。
  * 凭据只从环境变量读取（由集群 Secret 提供），本模块不写死任何凭据，
    渲染出的配置文件权限为 0600 且只落在随 Pod 生命周期销毁的内存卷里。
  * 不依赖实例元数据接口取临时身份：两端都使用静态访问密钥。
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import stat
import subprocess
import sys
from typing import Dict, List, Optional

# 对象键与桶名的白名单校验：校验不通过的内容一律不得拼进命令行。
SAFE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9!_.*'()/&$@=;:+ ,?\-]+$")
SAFE_BUCKET_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{1,61}[A-Za-z0-9]$")

# 任务消息的必备字段与类型
MESSAGE_REQUIRED_FIELDS = {
    "source_bucket": str,
    "dest_bucket": str,
    "keys": list,
}

PROGRESS_ANNOTATION = "transfer.xfer-rclone/progress"


def env_str(name: str, default: Optional[str] = None, required: bool = False) -> str:
    """读取字符串型环境变量。"""
    value = os.environ.get(name, default)
    if required and not value:
        raise RuntimeError("缺少必需的运行参数：%s" % name)
    return value or ""


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return int(str(raw).strip())
    except ValueError:
        raise RuntimeError("运行参数 %s 必须是整数，当前为 %r" % (name, raw))


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def configure_logging(component: str, level: Optional[str] = None) -> logging.Logger:
    """日志只写标准输出，由日志服务采集，不在容器内落文件。"""
    logging.basicConfig(
        level=getattr(logging, (level or os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    return logging.getLogger(component)


def obs_endpoint(region: str) -> str:
    """目的端区域服务端点，与 infra/outputs.tf 的 obs_endpoint 同构。"""
    return "obs.%s.myhuaweicloud.com" % region


def build_rclone_conf(include_dest: bool = True) -> str:
    """渲染搬运工具配置文本。

    源端：兼容对象存储协议的外部存储，端点与静态密钥由凭据对象注入。
    目的端：本实践的区域服务端点。三项访问参数由配置字典显式给出并被本函数强制校验，
            取错值直接失败，不带病消费：
              OBS_ENDPOINT          区域服务端点 obs.<region>.myhuaweicloud.com
              OBS_FORCE_PATH_STYLE  必须为 false（虚拟托管寻址；路径风格会被拒绝访问）
              OBS_SIGN_ALGORITHM    必须为 v4（低版本签名器会被判为签名类型无法识别）
    """
    region = env_str("HW_REGION", "cn-north-4", required=True)
    src_endpoint = env_str("SRC_ENDPOINT", required=True)
    src_access = env_str("SRC_ACCESS_KEY", required=True)
    src_secret = env_str("SRC_SECRET_KEY", required=True)

    force_path_style = env_str("OBS_FORCE_PATH_STYLE", "false").strip().lower()
    if force_path_style != "false":
        raise RuntimeError("OBS_FORCE_PATH_STYLE 只能取 false：目的端不接受路径风格寻址")
    sign_algorithm = env_str("OBS_SIGN_ALGORITHM", "v4").strip().lower()
    if sign_algorithm != "v4":
        raise RuntimeError("OBS_SIGN_ALGORITHM 只能取 v4：目的端不接受低版本签名器")
    src_provider = env_str("SRC_PROVIDER", "Other")

    lines: List[str] = [
        "# 由协调层在容器启动时生成，随 Pod 生命周期销毁。请勿提交本文件。",
        "[source]",
        "type = s3",
        "provider = %s" % src_provider,
        "env_auth = false",
        "access_key_id = %s" % src_access,
        "secret_access_key = %s" % src_secret,
        "endpoint = %s" % src_endpoint,
        "force_path_style = false",
        "",
    ]
    if include_dest:
        dest_endpoint = env_str("OBS_ENDPOINT") or obs_endpoint(region)
        dst_access = env_str("DST_ACCESS_KEY", required=True)
        dst_secret = env_str("DST_SECRET_KEY", required=True)
        lines += [
            "[dest]",
            "type = s3",
            "provider = %s" % env_str("OBS_PROVIDER", "HuaweiOBS"),
            "env_auth = false",
            "access_key_id = %s" % dst_access,
            "secret_access_key = %s" % dst_secret,
            "endpoint = %s" % dest_endpoint,
            "region = %s" % region,
            "force_path_style = false",
        ]
        if env_bool("OBS_NO_CHECK_BUCKET", True):
            lines.append("no_check_bucket = true")
        lines.append("")
    return "\n".join(lines)


def write_rclone_conf(include_dest: bool = True, logger: Optional[logging.Logger] = None) -> str:
    """把配置写到内存卷，权限收紧到属主可读写，返回路径。"""
    directory = env_str("RCLONE_CONFIG_DIR", "/etc/rclone")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, "rclone.conf")
    text = build_rclone_conf(include_dest=include_dest)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    os.environ["RCLONE_CONFIG"] = path
    if logger:
        # 只打印路径与远程名，不打印任何凭据内容
        logger.info("搬运工具配置已生成：%s（远程：source%s）", path, "、dest" if include_dest else "")
    return path


def copy_command(src_bucket: str, key: str, dst_bucket: str) -> List[str]:
    """单个对象的复制命令，参数口径与传输形态相关项一致。"""
    args = [
        "rclone",
        "copyto",
        "-v",
        "--stats",
        "30s",
        "--stats-one-line-date",
        "--s3-chunk-size",
        env_str("RCLONE_CHUNK_SIZE", "200M"),
        "--s3-upload-concurrency",
        str(env_int("RCLONE_UPLOAD_CONCURRENCY", 4)),
        "--buffer-size",
        env_str("RCLONE_BUFFER_SIZE", "64M"),
        "--retries",
        str(env_int("RCLONE_RETRIES", 3)),
        "--retries-sleep",
        env_str("RCLONE_RETRY_SLEEP", "1s"),
        "--update",
    ]
    bwlimit = env_str("RCLONE_BWLIMIT", "0")
    if bwlimit and bwlimit != "0":
        args += ["--bwlimit", bwlimit]
    if env_bool("VERIFY_CHECKSUM", False):
        args += ["--checksum"]
    cache_dir = env_str("RCLONE_CACHE_DIR")
    if cache_dir:
        args += ["--cache-dir", cache_dir]
    extra = env_str("RCLONE_EXTRA_ARGS")
    if extra:
        args += shlex.split(extra)
    args += ["source:%s/%s" % (src_bucket, key), "dest:%s/%s" % (dst_bucket, key)]
    return args


def list_command(bucket: str, prefix: str) -> List[str]:
    """源端清单命令：递归、只列对象、输出键名。"""
    target = "source:%s" % bucket
    if prefix:
        target = "%s/%s" % (target, prefix.strip("/"))
    return ["rclone", "lsf", target, "--files-only", "-R"]


def valid_bucket(name: str) -> bool:
    return bool(SAFE_BUCKET_PATTERN.match(name or ""))


def valid_key(key: str) -> bool:
    return bool(SAFE_KEY_PATTERN.match(key or ""))


def validate_message(body: Dict) -> List[str]:
    """返回结构非法原因列表；空列表表示可以交给传输流程处理。"""
    if not isinstance(body, dict):
        return ["消息体不是对象结构"]
    reasons: List[str] = []
    for field, expected in MESSAGE_REQUIRED_FIELDS.items():
        if field not in body:
            reasons.append("缺少字段 %s" % field)
        elif not isinstance(body[field], expected):
            reasons.append("字段 %s 类型应为 %s" % (field, expected.__name__))
    if not reasons:
        if not valid_bucket(body["source_bucket"]):
            reasons.append("源端桶名不合白名单规则")
        if not valid_bucket(body["dest_bucket"]):
            reasons.append("目的端桶名不合白名单规则")
        if not body["keys"]:
            reasons.append("对象键列表为空")
    return reasons


def emit_metric(logger: logging.Logger, name: str, value, **labels) -> None:
    """指标以标准输出的键值行形式给出，由日志服务按同一格式采集与聚合。"""
    parts = ["METRIC", "name=%s" % name, "value=%s" % value]
    parts += ["%s=%s" % (k, v) for k, v in sorted(labels.items())]
    logger.info(" ".join(parts))


def run(command: List[str], logger: logging.Logger, timeout: Optional[int] = None):
    """执行外部命令并返回 CompletedProcess，输出不截断地回写到日志。"""
    logger.debug("执行：%s", " ".join(command[:2]) + " ...")
    return subprocess.run(command, capture_output=True, timeout=timeout)
