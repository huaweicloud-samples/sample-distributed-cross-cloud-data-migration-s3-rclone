#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""副本进度回写：把已处理批次与对象数写到自己所在 Pod 的标注上。

为什么需要它：传输副本的吞吐指标默认只走标准输出，由日志服务采集；
运维侧要一眼看到「每个副本此刻处理了多少」，用标注比逐个翻日志快，
也让 src/jobs/status_transfer_job.sh 能直接汇总。

权限口径见 src/manifests/10-rbac.yaml：服务账号对 pods 具备 get/patch/update。
回写失败只记日志、不影响传输结果——进度是旁路信息，不能因为它把已成功的批次判为失败。
不引入额外的集群接口依赖：直接用挂载进来的服务账号令牌访问接口。
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from typing import Dict, Optional

TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


class ProgressReporter:
    def __init__(self, annotation_key: str, logger: Optional[logging.Logger] = None):
        self._annotation = annotation_key
        self._logger = logger or logging.getLogger("progress")
        self._api = os.environ.get("KUBERNETES_API") or "https://kubernetes.default.svc"
        self._namespace = os.environ.get("POD_NAMESPACE") or "transfer"
        self._pod = os.environ.get("POD_NAME") or ""
        self._enabled = bool(self._pod) and os.path.exists(TOKEN_PATH)
        if not self._enabled:
            self._logger.info("进度回写未启用（缺少 Pod 名称或服务账号令牌），指标只走标准输出")

    def report(self, payload: Dict) -> bool:
        """把进度写到 Pod 标注；返回是否成功，调用方不据此判定传输结果。"""
        if not self._enabled:
            return False
        token = open(TOKEN_PATH, "r", encoding="utf-8").read().strip()
        body = json.dumps(
            {"metadata": {"annotations": {self._annotation: json.dumps(payload, ensure_ascii=False)}}}
        ).encode("utf-8")
        request = urllib.request.Request(
            "%s/api/v1/namespaces/%s/pods/%s" % (self._api, self._namespace, self._pod),
            data=body,
            method="PATCH",
            headers={
                "Authorization": "Bearer %s" % token,
                "Content-Type": "application/merge-patch+json",
            },
        )
        context = ssl.create_default_context(cafile=CA_PATH if os.path.exists(CA_PATH) else None)
        if not os.path.exists(CA_PATH):
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(request, timeout=5, context=context) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self._logger.warning("进度标注回写失败，忽略：%s", exc)
            return False
