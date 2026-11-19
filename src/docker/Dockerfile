# 传输作业的两个组件镜像：列举组件与传输组件。
#
# 只装三样东西：
#   1. 搬运工具单二进制（版本由 ARG 固定，并按官方校验和核对）；
#   2. 任务队列的开源客户端（消息队列 4.8.0 版走原生 TCP 协议，需要原生运行库）；
#   3. 本仓库 src/jobs 下的协调脚本与离线自检脚本。
# 刻意不装批处理生态的对象存储连接器（jar/插件），也不装云厂商命令行工具；
# 渲染出的搬运配置里 env_auth 固定为 false —— 只使用配置文件内声明的访问密钥，
# 避免出现第二套凭据来源导致行为不可预期。
#
# 构建需要能访问搬运工具官方下载站与消息队列客户端的源码仓库；
# 内网构建时把 BASE_IMAGE / RCLONE_DOWNLOAD_BASE 指到内部镜像站与制品库，
# 或先把基础镜像搬到本账号的容器镜像服务组织里再构建。
#
# 构建（在仓库根目录执行，或直接使用 src/docker/build-and-push.sh）：
#   docker build --target lister -f src/docker/Dockerfile -t <仓库登录地址>/<组织>/lister:v1 .
#   docker build --target worker -f src/docker/Dockerfile -t <仓库登录地址>/<组织>/worker:v1 .
# 推送后的镜像地址即 infra/outputs.tf 输出的 lister_image / worker_image。

ARG BASE_IMAGE=docker.io/library/python:3.12-slim
ARG RCLONE_VERSION=v1.67.0
ARG RCLONE_DOWNLOAD_BASE=https://downloads.rclone.org
ARG ROCKETMQ_CLIENT_CPP_VERSION=2.0.0
ARG ROCKETMQ_CLIENT_PYTHON_VERSION=2.0.0
# 无法访问上述源码仓库时可置为 false：镜像仍能构建，协调脚本在接入队列时给出明确报错
ARG WITH_ROCKETMQ=true

# ---------------------------------------------------------------------------
# 搬运工具：固定版本并核对校验和
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS rclone

ARG RCLONE_VERSION
ARG RCLONE_DOWNLOAD_BASE

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl unzip; \
    rm -rf /var/lib/apt/lists/*; \
    mkdir -p /opt/rclone-stage; \
    curl -fsSL -o /tmp/rclone.zip \
      "${RCLONE_DOWNLOAD_BASE}/rclone-${RCLONE_VERSION}-linux-amd64.zip"; \
    curl -fsSL -o /tmp/checksums.txt "${RCLONE_DOWNLOAD_BASE}/checksums.txt"; \
    cd /tmp; \
    grep "rclone-${RCLONE_VERSION}-linux-amd64.zip\$" checksums.txt | sed 's|\*|-|' > expected.txt; \
    sha256sum -c expected.txt; \
    unzip -q rclone.zip -d /opt/rclone-stage; \
    install -m 0755 /opt/rclone-stage/rclone-*-linux-amd64/rclone /usr/local/bin/rclone; \
    rm -rf /tmp/rclone.zip /tmp/checksums.txt /tmp/expected.txt /opt/rclone-stage; \
    rclone version

# ---------------------------------------------------------------------------
# 运行底座：搬运工具 + 队列客户端 + 非特权运行身份
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS runtime

ARG ROCKETMQ_CLIENT_CPP_VERSION
ARG ROCKETMQ_CLIENT_PYTHON_VERSION
ARG WITH_ROCKETMQ

COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
COPY --chown=0:0 src/jobs/selftest.py /opt/transfer/jobs/selftest.py
COPY --chown=0:0 src/jobs/check_rclone_conf.py /opt/transfer/jobs/check_rclone_conf.py
COPY --chown=0:0 src/jobs/coordinator/ /opt/transfer/coordinator/

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl procps; \
    if [ "${WITH_ROCKETMQ}" = "true" ]; then \
      apt-get install -y --no-install-recommends build-essential cmake git libboost-all-dev; \
      git clone --depth 1 --branch "${ROCKETMQ_CLIENT_CPP_VERSION}" \
        https://github.com/apache/rocketmq-client-cpp.git /tmp/rocketmq-client-cpp; \
      cd /tmp/rocketmq-client-cpp; \
      sh build.sh; \
      find /tmp/rocketmq-client-cpp/build -name 'librocketmq.so*' -exec cp -av {} /usr/local/lib/ \; ; \
      ldconfig; \
      pip install --no-cache-dir "rocketmq-client-python==${ROCKETMQ_CLIENT_PYTHON_VERSION}"; \
      apt-get purge -y --auto-remove build-essential cmake git libboost-all-dev; \
    fi; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/rocketmq-client-cpp /root/.cache/pip; \
    useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin transfer; \
    install -d -m 0750 -o transfer -g transfer /opt/transfer /var/cache/rclone /var/run/transfer; \
    chown -R transfer:transfer /opt/transfer

WORKDIR /opt/transfer

# 构建期离线自检：脚本可导入、配置三项访问参数正确、切批与确认/重投/死信逻辑可用。
# 自检全程使用占位凭据与本地文件队列，不连接任何远端服务。
USER transfer
RUN set -eux; \
    rclone version >/dev/null; \
    python3 /opt/transfer/jobs/selftest.py

ENTRYPOINT []

# ---------------------------------------------------------------------------
# 列举组件：分页读取源端清单并按批投递任务消息
# ---------------------------------------------------------------------------
FROM runtime AS lister
CMD ["python3", "-u", "/opt/transfer/coordinator/lister_producer.py"]

# ---------------------------------------------------------------------------
# 传输组件：固定副本数，逐批领取任务消息并写入目的端存储桶
# ---------------------------------------------------------------------------
FROM runtime AS worker
CMD ["/bin/bash", "/opt/transfer/coordinator/worker_entrypoint.sh"]
