#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""搬运工具配置的静态核对。

核对三项对象存储访问参数与凭据落盘口径，任一项不符即失败退出：
  1. 端点：目的端必须使用区域服务端点 obs.<region>.myhuaweicloud.com；
  2. 寻址：两端都必须是虚拟托管风格（force_path_style = false），路径风格会被拒绝访问；
  3. 签名：目的端 provider 选定对象存储服务后固定使用 V4 签名，
           配置里不得出现将签名降到 V2 的开关，也不得启用读取环境凭证链（env_auth），
           凭据只由凭据对象注入到环境后渲染进配置。

两种模式：
  --mode sample  离线渲染产物：凭据必须是占位符，禁止出现真实值；
  --mode runtime 容器运行期：凭据必须已注入（非空、非占位符）。

用法：
    python3 src/jobs/check_rclone_conf.py --file .rendered/rclone.conf --mode sample
    python3 src/jobs/check_rclone_conf.py --file /etc/rclone/rclone.conf --mode runtime
"""

from __future__ import annotations

import argparse
import configparser
import os
import re
import sys
from typing import List, Optional

PLACEHOLDER_PATTERN = re.compile(r"^<[^<>]+>$")
V2_SIGNATURE_PATTERN = re.compile(r"(^|[=\s-])v2", re.IGNORECASE)


def _load(path: str) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    with open(path, "r", encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser


def check(path: str, mode: str, region: Optional[str] = None) -> List[str]:
    problems: List[str] = []
    if not os.path.isfile(path):
        return ["配置文件不存在：%s" % path]

    parser = _load(path)
    if "source" not in parser.sections():
        problems.append("缺少源端远程 [source]")
    if "dest" not in parser.sections():
        problems.append("缺少目的端远程 [dest]")
    if problems:
        return problems

    expected_region = region or os.environ.get("HW_REGION") or ""

    for name in ("source", "dest"):
        section = parser[name]
        if section.get("type", "").strip() != "s3":
            problems.append("[%s] type 必须是 s3（对象存储的兼容协议名）" % name)
        path_style = section.get("force_path_style")
        if path_style is None:
            problems.append("[%s] 未显式声明 force_path_style，无法确认虚拟托管寻址" % name)
        elif path_style.strip().lower() != "false":
            problems.append("[%s] force_path_style 必须为 false（路径风格会被目的端拒绝）" % name)
        if section.get("env_auth", "false").strip().lower() == "true":
            problems.append("[%s] 不得启用环境凭证链读取，凭据只经凭据对象注入后渲染" % name)
        for key in ("access_key_id", "secret_access_key"):
            value = (section.get(key) or "").strip()
            is_placeholder = bool(PLACEHOLDER_PATTERN.match(value))
            if not value:
                problems.append("[%s] %s 为空" % (name, key))
            elif mode == "sample" and not is_placeholder:
                problems.append("[%s] %s 在离线产物里必须是占位符" % (name, key))
            elif mode == "runtime" and is_placeholder:
                problems.append("[%s] %s 仍是占位符，凭据对象未注入真实值" % (name, key))
        if V2_SIGNATURE_PATTERN.search(section.get("provider", "") + " " + (section.get("endpoint") or "")):
            problems.append("[%s] 出现把签名降到 V2 的配置痕迹" % name)

    source_endpoint = parser["source"].get("endpoint", "").strip()
    if not source_endpoint:
        problems.append("[source] endpoint 为空，源端必须给出可访问的服务域名")

    dest_section = parser["dest"]
    dest_endpoint = dest_section.get("endpoint", "").strip()
    if expected_region:
        expected = "obs.%s.myhuaweicloud.com" % expected_region
        if dest_endpoint != expected:
            problems.append("[dest] endpoint 应为区域服务端点 %s，当前为 %r" % (expected, dest_endpoint))
    elif not dest_endpoint.startswith("obs.") or not dest_endpoint.endswith(".myhuaweicloud.com"):
        problems.append("[dest] endpoint 不符合区域服务端点形态 obs.<region>.myhuaweicloud.com")

    if dest_section.get("provider", "").strip() != "HuaweiOBS":
        problems.append("[dest] provider 必须为 HuaweiOBS：该取值同时固定了 V4 签名与虚拟托管寻址")
    dest_region = dest_section.get("region", "").strip()
    if expected_region and dest_region != expected_region:
        problems.append("[dest] region 应为 %s，当前为 %r" % (expected_region, dest_region))

    return problems


def main(argv: List[str] = ()) -> int:
    parser = argparse.ArgumentParser(description="静态核对搬运工具配置")
    parser.add_argument("--file", required=True, help="待核对的配置文件")
    parser.add_argument("--mode", choices=("sample", "runtime"), default="sample")
    parser.add_argument("--region", default="", help="目的端区域，缺省时读取 HW_REGION")
    args = parser.parse_args(list(argv) or None)

    problems = check(args.file, args.mode, args.region or None)
    if problems:
        for item in problems:
            print("配置核对不通过：%s" % item, file=sys.stderr)
        return 1
    print("配置核对通过：端点、寻址风格与签名方式三项均符合要求（%s）" % args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
