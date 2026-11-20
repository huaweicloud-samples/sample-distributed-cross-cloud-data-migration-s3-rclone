# 部署指导

本文给出从零到可验证部署的完整命令与每步预期输出。主线分六步：建基础设施 → 构建并推送两个镜像 → 渲染配置与清单 → 提交搬运作业 → 查进度 → 释放资源。

部署区域为 `cn-north-4`。文中尖括号内容一律是占位符，请替换成自己环境的取值；`<源端桶名>`、`<AK>`、`<SK>` 等占位符在任何文件中都不要写成真实值。

## 0. 准备

**工具**

| 工具 | 版本要求 | 用途 |
|-|-|-|
| Terraform | `>= 1.5` | 创建与销毁基础设施（`infra/versions.tf` 声明） |
| huaweicloud Provider | `~> 1.96` | 同上 |
| kubectl | 与集群版本兼容 | 应用清单、观察作业与副本 |
| Python | 3，组件镜像内为 3.12 | 渲染清单、离线自检与配置核对 |
| Docker 或 Podman | 支持多阶段构建 | 构建两个组件镜像（可用 `CONTAINER_CLI=podman` 切换） |

**取值清单**（部署前先收集齐）

- 源端对象存储的服务域名 `<源端服务域名>`、访问密钥 ID `<AK>`、私密访问密钥 `<SK>`、桶名 `<源端桶名>`、可选键前缀 `<键前缀>`。
- 目的端桶名：`dest_bucket_name` 的默认值 `xfer-rclone-dest-please-rename` 属全域占位名，必须改成未被占用的唯一名称。
- 节点登录方式：`node_password` 与 `node_key_pair` 二选一，只能填一个，否则 `variables.tf` 里的 `terraform_data.login_guard` 前置校验会直接拒绝。
- 镜像仓库登录身份：`SWR_USERNAME`（形如 `<租户名>@<组织名>`）与 `SWR_PASSWORD`（控制台「登录指令」给出的口令）。
- 访问密钥在华为云「我的凭证」页面获取。Terraform 侧用 `HW_ACCESS_KEY`、`HW_SECRET_KEY` 两个取值提供给命令行；不要把凭据写入代码或提交到仓库。

## 1. 建基础设施

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
```

编辑 `terraform.tfvars`，至少改掉 `dest_bucket_name` 与节点登录方式两项；`name_prefix` 同时作为容器镜像服务的组织名，需账号内唯一。

```bash
export HW_ACCESS_KEY='<HW_ACCESS_KEY>'
export HW_SECRET_KEY='<HW_SECRET_KEY>'
terraform init
terraform validate
terraform apply -var-file=terraform.tfvars
```

**预期输出**

`terraform init` 结尾打印 `Terraform has been successfully initialized!`。

`terraform validate` 打印：

```
Success! The configuration is valid.
```

`terraform apply` 需要输入 `yes` 确认，结束后打印计划与 39 个受管资源的输出表（节点池创建的云服务器与云硬盘由容器引擎侧维护，不计入该数字）：

```
Apply complete! Resources: 39 added, 0 changed, 0 destroyed.

Outputs:
cluster_id             = "..."
dest_bucket            = "xfer-rclone-dest-<你的唯一后缀>"
obs_endpoint           = "obs.cn-north-4.myhuaweicloud.com"
rocketmq_namesrv_address = "10.0.11.x:8100"
task_topic             = "xfer-rclone-transfer-task"
worker_consumer_group  = "xfer-rclone-worker-group"
dead_letter_topic      = "xfer-rclone-transfer-dlq"
native_dead_letter_topic = "%DLQ%xfer-rclone-worker-group"
lister_image           = "swr.cn-north-4.myhuaweicloud.com/xfer-rclone/lister:v1"
worker_image           = "swr.cn-north-4.myhuaweicloud.com/xfer-rclone/worker:v1"
credential_names       = ["xfer-rclone-source-endpoint", ...]
...
```

**记下后续要用的输出**

```bash
terraform output submit_job_command
```

这条输出直接给出与提交脚本一一对应的整行取值：

```
SRC_BUCKET=<源端桶名> DST_BUCKET=xfer-rclone-dest-<你的唯一后缀> PREFIX=<源端键前缀，可留空> ROCKETMQ_NAMESRV=10.0.11.x:8100 TASK_TOPIC=xfer-rclone-transfer-task CONSUMER_GROUP=xfer-rclone-worker-group DEAD_LETTER_TOPIC=xfer-rclone-transfer-dlq LISTER_IMAGE=swr.cn-north-4.myhuaweicloud.com/xfer-rclone/lister:v1 WORKER_IMAGE=swr.cn-north-4.myhuaweicloud.com/xfer-rclone/worker:v1 OBS_ENDPOINT=obs.cn-north-4.myhuaweicloud.com bash src/jobs/submit_transfer_job.sh
```

其余常用输出：`vpc_id`、`node_subnet_ids`、`security_group_id`、`egress_public_ip`、`lister_node_pool_id`、`worker_node_pool_id`、`namespace`、`dest_bucket_domain`、`rocketmq_instance_id`、`rocketmq_broker_address`、`worker_replicas`、`swr_login_server`、`log_group_id`、`lister_log_stream`、`worker_log_stream`。

**写入凭据真实值（手工前置动作）**

`infra/secrets.tf` 创建的 5 条凭据只含占位内容，名称由 `terraform output credential_names` 列出：

| 凭据名称 | 占位内容 | 应填入 |
|-|-|-|
| `xfer-rclone-source-endpoint` | `<SRC_ENDPOINT>` | 源端服务域名 |
| `xfer-rclone-source-access-key` | `<SRC_ACCESS_KEY>` | 源端访问密钥 ID |
| `xfer-rclone-source-secret-key` | `<SRC_SECRET_KEY>` | 源端私密访问密钥 |
| `xfer-rclone-dest-access-key` | `<HW_ACCESS_KEY>` | 本账号访问密钥 ID |
| `xfer-rclone-dest-secret-key` | `<HW_SECRET_KEY>` | 本账号私密访问密钥 |

在凭据管理系统控制台逐条编辑，或用命令行更新。这几条凭据对声明了「运行期写入的值不再回写」，后续 `terraform apply` 不会把真实值覆盖回占位内容。

## 2. 构建并推送两个镜像

容器镜像服务侧已由上一步建好组织 `<name_prefix>` 与两个私有仓库 `lister` / `worker`。镜像由使用者本地构建后推送，这一步不能省略。

```bash
cd ..
export SWR_USERNAME='<租户名>@<组织名>'
export SWR_PASSWORD='<控制台登录指令给出的口令>'
bash src/docker/build-and-push.sh
```

可覆盖的取值：`HW_REGION`、`NAME_PREFIX`、`IMAGE_TAG`、`SWR_LISTER_IMAGE`、`SWR_WORKER_IMAGE`、`SWR_LOGIN_SERVER`、`BASE_IMAGE`、`RCLONE_VERSION`、`WITH_ROCKETMQ`、`CONTAINER_CLI`。

**预期输出**

```
[1/4] 登录镜像仓库 swr.cn-north-4.myhuaweicloud.com（账号 <租户名>@<组织名>，口令不回显）
[2/4] 构建列举组件镜像 -> swr.cn-north-4.myhuaweicloud.com/xfer-rclone/lister:v1
[3/4] 构建传输组件镜像 -> swr.cn-north-4.myhuaweicloud.com/xfer-rclone/worker:v1
[4/4] 推送两个镜像

两个镜像已推送：
  LISTER_IMAGE='swr.cn-north-4.myhuaweicloud.com/xfer-rclone/lister:v1'
  WORKER_IMAGE='swr.cn-north-4.myhuaweicloud.com/xfer-rclone/worker:v1'
```

构建过程会跑一遍离线自检（`rclone version` 与 `selftest.py`），自检不通过时构建直接失败。若无法访问 rclone 下载站或消息队列客户端源码仓，把 `BASE_IMAGE` 与 `RCLONE_DOWNLOAD_BASE` 指到内部制品库，或先用 `WITH_ROCKETMQ=false` 构建（此时镜像仍能产出，但副本接入队列会给出明确报错）。

缺 `SWR_USERNAME` / `SWR_PASSWORD` 时脚本立即退出：

```
构建中止：缺少 SWR_USERNAME（形如 <租户名>@<组织名>）
```

## 3. 渲染配置与清单

**准备集群访问方式（二选一）**

已有 `KUBECONFIG`：

```bash
export KUBECONFIG=$HOME/.kube/config
```

或用集群编号换取访问凭证（需要云命令行 `hw`）：

```bash
export CLUSTER_ID="$(cd infra && terraform output -raw cluster_id)"
```

提交脚本会自动执行 `hw cce get-credentials --cluster-id <集群编号> --kubeconfig ./kubeconfig.yaml` 并改用该凭证文件。

**先取一次队列接入地址**

```bash
cd infra && terraform output -raw rocketmq_namesrv_address && cd ..
```

**渲染并核对**

```bash
export HW_REGION=cn-north-4 NAME_PREFIX=xfer-rclone IMAGE_TAG=v1
export SRC_BUCKET='<源端桶名>' DST_BUCKET='xfer-rclone-dest-<你的唯一后缀>' PREFIX='<键前缀>'
export ROCKETMQ_NAMESRV='<上一步输出的接入地址>'

python3 src/jobs/render_manifests.py --check-only
```

预期首行与取值表（取值优先级：环境变量 > 与 `infra/terraform.tfvars.example` 一致的默认值）：

```
模板校验通过：5 份清单的占位符全部可注入
  HW_REGION                    = cn-north-4
  OBS_ENDPOINT                 = obs.cn-north-4.myhuaweicloud.com
  TASK_TOPIC                   = xfer-rclone-transfer-task
  CONSUMER_GROUP               = xfer-rclone-worker-group
  WORKER_REPLICAS              = 1
  WORKER_PROCESSES_PER_REPLICA = 6
  BATCH_SIZE                   = 20
  RCLONE_CHUNK_SIZE            = 200M
  LISTER_IMAGE                 = swr.cn-north-4.myhuaweicloud.com/xfer-rclone/lister:v1
  WORKER_IMAGE                 = swr.cn-north-4.myhuaweicloud.com/xfer-rclone/worker:v1
```

缺少必需取值时以退出码 2 结束并逐条给出原因：

```
参数校验不通过：缺少必需参数 SRC_BUCKET（由 infra 输出或提交环境提供）
参数校验不通过：目的端桶名仍是占位默认值 xfer-rclone-dest-please-rename；桶名全域唯一，请先修改 infra 的 dest_bucket_name 并重新部署，再以新的 DST_BUCKET 提交作业
```

**写出渲染产物**

```bash
python3 src/jobs/render_manifests.py --out .rendered
python3 src/jobs/check_rclone_conf.py --file .rendered/rclone.conf --mode sample --region cn-north-4
```

预期输出：

```
已渲染 5 份清单到 .rendered
离线核对用的搬运工具配置：.rendered/rclone.conf（凭据为占位符，不含真实值）
配置核对通过：端点、寻址风格与签名方式三项均符合要求（.rendered/rclone.conf）
```

`.rendered/` 下得到 `00-namespace.yaml`、`10-rbac.yaml`、`20-transfer-config.yaml`、`30-lister-job.yaml`、`40-worker-deployment.yaml` 与 `rclone.conf`，只含名称与参数、不含任何凭据，属本地产物，不要提交。核对项固定三条：目的端端点必须是 `obs.<region>.myhuaweicloud.com`；两端 `force_path_style` 必须是 `false`；目的端 `provider` 必须是 `HuaweiOBS`，且配置里不得出现把签名降到 V2 的痕迹或读取环境凭证的开关。

**离线自检（可与上面并列执行）**

```bash
cd src/jobs && python3 selftest.py && cd ../..
```

预期 26 项断言全部 `PASS`，末行：

```
自检全部通过：配置三项访问参数、切批、确认与重投、死信转发均符合预期（全程未连接远端服务）
```

自检覆盖四类内容：配置渲染与静态核对、列举切批与队列投递、副本消费与重投/死信转发、列举侧寻址与两端路径拼接，全程使用占位凭据与本地文件队列，不连接任何远端服务。

## 4. 提交搬运作业

```bash
export LISTER_IMAGE='swr.cn-north-4.myhuaweicloud.com/xfer-rclone/lister:v1'
export WORKER_IMAGE='swr.cn-north-4.myhuaweicloud.com/xfer-rclone/worker:v1'
export TASK_TOPIC=xfer-rclone-transfer-task
export CONSUMER_GROUP=xfer-rclone-worker-group
export DEAD_LETTER_TOPIC=xfer-rclone-transfer-dlq
export OBS_ENDPOINT=obs.cn-north-4.myhuaweicloud.com

export SRC_ENDPOINT='<源端服务域名>' SRC_ACCESS_KEY='<AK>' SRC_SECRET_KEY='<SK>'
export HW_ACCESS_KEY='<HW_ACCESS_KEY>' HW_SECRET_KEY='<HW_SECRET_KEY>'
export SWR_USERNAME='<租户名>@<组织名>' SWR_PASSWORD='<登录指令给出的口令>'

bash src/jobs/submit_transfer_job.sh
```

可选覆盖：`NAME_PREFIX`、`HW_REGION`、`WORKER_REPLICAS`、`WORKER_PROCESSES_PER_REPLICA`、`BATCH_SIZE`、`RCLONE_CHUNK_SIZE`、`VERIFY_CHECKSUM`、`WORKER_BUFFER_GB`、`RENDER_DIR`、`NAMESPACE`、`CLUSTER_ID`、`TRACK_TIMEOUT`。

**预期输出（八个步骤）**

```
[1/8] 前置检查
[1/8] 通过云命令行换取集群访问凭证            # 仅在提供 CLUSTER_ID 时出现
[2/8] 渲染作业清单
[3/8] 离线静态核对
YAML 语法与必备字段核对通过：5 份清单
配置核对通过：端点、寻址风格与签名方式三项均符合要求（.rendered/rclone.conf）
[4/8] 应用命名空间内的访问模型与运行参数
namespace/transfer unchanged
serviceaccount/transfer-worker created
role.rbac.authorization.k8s.io/transfer-worker created
rolebinding.rbac.authorization.k8s.io/transfer-worker created
configmap/xfer-rclone-transfer-config created
[5/8] 创建凭据对象
五个凭据对象已按环境变量写入（值不落盘、不打印）
私有镜像仓库拉取凭证已就绪
[6/8] 拉起传输副本并投递列举作业
deployment.apps/xfer-rclone-worker created
Waiting for deployment "xfer-rclone-worker" rollout to finish: 0 of 1 intended replicas are available
deployment "xfer-rclone-worker" successfully rolled out
job.batch/xfer-rclone-lister created
[7/8] 跟踪完成情况
列举作业输出对象清单并切批投递，传输副本并行领取。Ctrl-C 可中断跟踪，稍后用 status_transfer_job.sh 继续查看。
[8/8] 汇总报告
```

第 5 步在任一凭据取值为空或仍是占位符时不会创建凭据对象，而是打印并沿用集群里已有的对象：

```
未提供 SRC_SECRET_KEY，跳过凭据创建，假定集群中对应凭据对象已写入真实值
```

这样能保证不会用占位内容覆盖运行中的真实凭据。

第 7 步阻塞等待列举作业完成，默认上限 `TRACK_TIMEOUT=3600s`；超时只提示不失败：

```
列举作业在 3600s 内未完成，可继续用 status_transfer_job.sh 观察
```

前置条件不满足时立即中止，例如：

```
提交中止：缺少环境变量 ROCKETMQ_NAMESRV（可从 infra 输出获取，见部署指导）
提交中止：命名空间 transfer 不存在，请先完成 infra 部署或执行 kubectl apply -f src/manifests/00-namespace.yaml
提交中止：传输副本未就绪，先检查镜像是否已推送到私有仓库
```

## 5. 查进度

```bash
bash src/jobs/status_transfer_job.sh            # 单次快照
WATCH_INTERVAL=15 bash src/jobs/status_transfer_job.sh --watch   # 周期刷新
```

**预期输出（六节）**

```
=== 列举作业 ===
NAME                STATUS     COMPLETIONS   DURATION   AGE
xfer-rclone-lister  Complete   1/1           84s        2m
INFO [lister] SUMMARY component=lister source_bucket=<源端桶名> dest_bucket=xfer-rclone-dest-<...> prefix='data/2024/' keys_total=45 messages_total=3

=== 传输副本 ===
NAME                READY   UP-TO-DATE   AVAILABLE   AGE
xfer-rclone-worker  1/1     1            1           2m
POD                          READY   RESTARTS   NODE   AGE
xfer-rclone-worker-...       true    0          ...    2m

=== 每副本进度标注 ===
xfer-rclone-worker-...   已确认批次 3 / 成功对象 45 / 失败对象 0 / 待重试批次 0
合计：已确认批次 3，成功对象 45，失败对象 0，待重试批次 0

=== 指标行汇总（近 200 行日志） ===
      3 METRIC name=batch_duration_seconds value=12.4
      3 METRIC name=objects_transferred value=20

=== 最近失败线索 ===
近期无错误或告警日志

=== 队列与死信查看线索 ===
任务主题        xfer-rclone-transfer-task   积压量与消费延迟在队列实例页查看
业务侧死信主题  xfer-rclone-transfer-dlq    这里只收结构非法、已确认丢弃的消息
消费组          xfer-rclone-worker-group    重投上限 2
原生死信主题    %DLQ%xfer-rclone-worker-group   超过重投上限后由队列投递，需人工处理
```

最后一节的名称取自生效中的 ConfigMap `xfer-rclone-transfer-config`，可直接照抄到消息队列控制台；配置映射尚未创建时该节会提示先在队列实例页按实例查看主题与消费组。

队列侧积压量、消费延迟与死信数量在分布式消息服务控制台按实例 `xfer-rclone-queue` 查看；容器标准输出已由 `infra/lts.tf` 采集到日志流 `xfer-rclone-lister` 与 `xfer-rclone-worker`，控制台里可按同一 `SUMMARY` / `METRIC` 格式检索。

## 6. 释放资源

先停作业，再删运行参数与凭据，最后销毁基础设施。

```bash
kubectl delete job xfer-rclone-lister -n transfer
kubectl delete deployment xfer-rclone-worker -n transfer
kubectl delete secret xfer-rclone-swr-pull -n transfer
kubectl delete configmap xfer-rclone-transfer-config -n transfer
kubectl delete secret xfer-rclone-source-endpoint xfer-rclone-source-access-key \
  xfer-rclone-source-secret-key xfer-rclone-dest-access-key xfer-rclone-dest-secret-key -n transfer
```

```bash
cd infra
terraform destroy -var-file=terraform.tfvars
```

预期输出：

```
Plan: 0 to add, 0 to change, 39 to destroy.
...
Destroy complete! Resources: 39 destroyed.
```

**两处需要注意的销毁顺序**

- 容器镜像服务侧必须先删仓库再删组织。整仓销毁受阻时先执行：

  ```bash
  terraform destroy -target=huaweicloud_swr_repository.lister -target=huaweicloud_swr_repository.worker
  terraform destroy
  ```

- 目的端桶在 `force_destroy_bucket = false`（默认）且桶内有对象时会拒绝销毁，这是防止误删已落盘的数据。确认数据不再需要时，把 `force_destroy_bucket` 置为 `true` 后先 `terraform apply` 再 `terraform destroy`。

凭据管理系统里的凭据与 KMS 主密钥销毁后进入 7 天待删除期，期间可取消。及时释放资源，避免残留。

## 7. 部署后验证清单

按 [README 的使用方法](../README.md#使用方法与验证) 逐条在真实环境确认，三项都通过才算交付完成：

- [ ] 列举作业的 `SUMMARY` 行里 `keys_total` 与源端预期对象数一致，`messages_total` 等于对象数除以 `BATCH_SIZE` 向上取整。
- [ ] 「每副本进度标注」的合计行里 `失败对象 0`、`待重试批次 0`，且各副本 `已确认批次` 之和等于 `messages_total`。
- [ ] 目的端 OBS 桶内对象数与总大小符合预期；抽查若干对象的内容或校验和一致。开启 `VERIFY_CHECKSUM=true` 时以校验和比对为准。
- [ ] 消息队列控制台里任务主题积压归零，业务侧死信主题与原生死信主题均无待处理消息。
