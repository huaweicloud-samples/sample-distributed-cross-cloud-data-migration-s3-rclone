# ============================================================================
# 输入变量
# ============================================================================
# 所有决定资源规格的变量均显式赋值（不使用数据源下标挑选规格），
# 便于部署前逐项核对规格与容量。

variable "region" {
  description = "资源部署区域"
  type        = string
  default     = "cn-north-4"
}

variable "name_prefix" {
  description = "资源命名前缀（同时作为容器镜像服务的组织名，需账号内唯一）"
  type        = string
  default     = "xfer-rclone"
}

# ---------------------------------------------------------------------------
# 网络
# ---------------------------------------------------------------------------

variable "vpc_cidr" {
  description = "虚拟私有云地址段"
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidrs" {
  description = "三个可用区对应的子网地址段（顺序与 availability_zones 一致）"
  type        = list(string)
  default = [
    "10.0.10.0/24",
    "10.0.11.0/24",
    "10.0.12.0/24",
  ]
}

variable "availability_zones" {
  description = "三个可用区名称"
  type        = list(string)
  default = [
    "cn-north-4a",
    "cn-north-4b",
    "cn-north-4c",
  ]
}

variable "nat_spec" {
  description = "NAT 网关规格：1=小型 / 2=中型 / 3=大型 / 4=超大型"
  type        = string
  default     = "1"
}

variable "nat_eip_bandwidth_size" {
  description = "出网带宽上限（Mbit/s），决定同步吞吐上限"
  type        = number
  default     = 100
}

# ---------------------------------------------------------------------------
# 对象存储（目的端）
# ---------------------------------------------------------------------------

variable "dest_bucket_name" {
  description = "目的端对象存储桶名（全域唯一，部署前请改为未被占用的名称）"
  type        = string
  default     = "xfer-rclone-dest-please-rename"
}

variable "force_destroy_bucket" {
  description = "销毁时是否自动清空桶内容。仅在本桶为一次性验证用途时设为 true"
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# 消息队列（分布式消息服务 RocketMQ 版）
# ---------------------------------------------------------------------------

variable "rocketmq_engine_version" {
  description = "RocketMQ 实例引擎版本"
  type        = string
  default     = "4.8.0"
}

variable "rocketmq_flavor_id" {
  description = "RocketMQ 实例产品规格（显式指定，不依赖数据源返回顺序）"
  type        = string
  default     = "c6.4u8g.cluster"
}

variable "rocketmq_storage_spec_code" {
  description = "RocketMQ 存储 I/O 规格"
  type        = string
  default     = "dms.physical.storage.high.v2"
}

variable "rocketmq_broker_num" {
  description = "Broker 数量"
  type        = number
  default     = 1
}

variable "rocketmq_storage_space" {
  description = "每个 Broker 的消息存储容量（GB）"
  type        = number
  default     = 300
}

variable "retry_max_times" {
  description = "同一批对象的最大重投次数，超过后进入死信队列"
  type        = number
  default     = 2
}

# ---------------------------------------------------------------------------
# 容器引擎（CCE）
# ---------------------------------------------------------------------------

variable "cce_control_flavor" {
  description = "集群控制面规格"
  type        = string
  default     = "cce.s1.small"
}

variable "cce_cluster_version" {
  description = "集群版本，留空由平台选择当前可创建的版本"
  type        = string
  default     = ""
}

variable "container_network_cidr" {
  description = "容器网络地址段，不得与 vpc_cidr 重叠"
  type        = string
  default     = "172.16.0.0/16"
}

variable "service_network_cidr" {
  description = "Service 地址段，不得与 vpc_cidr、容器网络地址段重叠"
  type        = string
  default     = "10.254.0.0/16"
}

variable "lister_node_flavor" {
  description = "列举节点池的节点规格"
  type        = string
  default     = "c7.large.2"
}

variable "worker_node_flavor" {
  description = "传输节点池的节点规格：内存优化型 4 vCPU / 32 GiB（1:8），高网络基线决定单节点吞吐上限，规格显式写死"
  type        = string
  default     = "m7.xlarge.8"

  validation {
    # 传输节点池固定落在 availability_zones[1]（cn-north-4b）。实测：r7 家族在
    # cn-north-4 全区域不可售；am7 在 cn-north-4b 不可售。m7.xlarge.8 为
    # 4 vCPU / 32 GiB（1:8）内存优化型，且在 cn-north-4b 可售。
    condition     = !startswith(var.worker_node_flavor, "r7.")
    error_message = "r7 家族在 cn-north-4 不可售，传输节点请改用 m7 等可售的内存优化型规格。"
  }
}

variable "node_os" {
  description = "节点操作系统，取值需与集群版本兼容"
  type        = string
  default     = "Ubuntu 22.04"
}

variable "node_root_volume_size" {
  description = "节点系统盘容量（GB）"
  type        = number
  default     = 50
}

variable "node_root_volume_type" {
  description = "节点系统盘类型"
  type        = string
  default     = "SSD"
}

variable "worker_node_data_volume_size" {
  description = "传输节点数据盘容量（GB），用于 rclone 分片缓冲"
  type        = number
  default     = 100
}

variable "node_password" {
  description = "节点登录密码（与 node_key_pair 二选一）。无默认值，部署前必须显式提供"
  type        = string
  sensitive   = true
  default     = ""
}

variable "node_key_pair" {
  description = "已存在的节点 SSH 密钥对名称（与 node_password 二选一）"
  type        = string
  default     = ""
}

variable "use_pinned_node_image" {
  description = "是否用锚定名称匹配出的公共镜像固定节点镜像"
  type        = bool
  default     = false
}

variable "node_image_name_regex" {
  description = "节点镜像名称匹配表达式，必须首尾锚定，避免命中带显卡驱动的变体"
  type        = string
  default     = "^Ubuntu 22.04 server 64bit$"
}

# ---------------------------------------------------------------------------
# 作业形态
# ---------------------------------------------------------------------------

variable "worker_replicas" {
  description = "传输副本数。本实践为固定副本数，由该变量决定，不随队列积压自动增减"
  type        = number
  default     = 1
}

variable "worker_processes_per_replica" {
  description = "每个副本内并行的 rclone 进程数"
  type        = number
  default     = 6
}

variable "batch_size" {
  description = "每条任务消息携带的对象数量"
  type        = number
  default     = 20
}

variable "rclone_chunk_size" {
  description = "rclone 分片大小"
  type        = string
  default     = "200M"
}

variable "swr_lister_image" {
  description = "列举组件在容器镜像服务中的仓库路径"
  type        = string
  default     = "lister"
}

variable "swr_worker_image" {
  description = "传输组件在容器镜像服务中的仓库路径"
  type        = string
  default     = "worker"
}

variable "image_tag" {
  description = "组件镜像标签"
  type        = string
  default     = "v1"
}

variable "log_retention_days" {
  description = "日志保留天数"
  type        = number
  default     = 7
}

variable "enable_lts_cce_access" {
  description = "是否创建容器标准输出到日志流的接入配置（huaweicloud_lts_cce_access）。关闭后日志组与日志流仍会创建，只是不接采集规则。"
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

# 节点登录方式必须显式二选一，否则创建节点池会被拒绝
resource "terraform_data" "login_guard" {
  input = {
    password_given = var.node_password != ""
    keypair_given  = var.node_key_pair != ""
  }

  lifecycle {
    precondition {
      condition     = (var.node_password != "") != (var.node_key_pair != "")
      error_message = "node_password 与 node_key_pair 必须且只能提供一个。"
    }
    precondition {
      condition     = length(var.subnet_cidrs) == length(var.availability_zones)
      error_message = "subnet_cidrs 与 availability_zones 数量必须一致。"
    }
    precondition {
      condition     = var.worker_replicas >= 1
      error_message = "worker_replicas 至少为 1：本实践的传输副本数为固定值，不会随积压自动扩容。"
    }
  }
}
