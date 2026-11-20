#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按注入的凭据渲染搬运工具配置文件。

副本容器由 src/jobs/coordinator/worker_entrypoint.sh 在启动并行进程前调用一次，
列举作业在开始时调用（只渲染源端远程）。凭据只从环境变量取，配置写进内存卷，
权限 0600，随 Pod 销毁；渲染过程与结果都不打印凭据内容。

对象存储访问要求的三项参数（区域服务端点、虚拟托管寻址、V4 签名）由
transfer_common.build_rclone_conf 固化，并由 check_rclone_conf.py 静态校验。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from transfer_common import configure_logging, write_rclone_conf  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染搬运工具配置")
    parser.add_argument("--source-only", action="store_true", help="只渲染源端远程（列举作业用）")
    parser.add_argument("--print-path", action="store_true", help="只打印配置文件路径")
    args = parser.parse_args()

    logger = configure_logging("render-config")
    path = write_rclone_conf(include_dest=not args.source_only, logger=None if args.print_path else logger)
    if args.print_path:
        print(path)
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main())
