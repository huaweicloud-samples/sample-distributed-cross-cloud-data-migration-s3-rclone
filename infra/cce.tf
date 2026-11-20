# ============================================================================
# 容器引擎：集群、节点池、命名空间
# ============================================================================
# 列举组件与传输组件分别调度到独立节点池，互不抢占传输带宽。
# 传输副本数为固定值（var.worker_replicas），不随队列积压自动增减。
# 节点未单独指定安全组，统一继承集群安全组。

# 节点以委托方式获得对象存储与镜像仓库访问权限
resource "huaweicloud_iam_agency" "cce" {
  name                  = "${var.name_prefix}-cce-agency"
  description           = "容器引擎节点代维委托"
  delegated_domain_name = "op_svc_cce"

  project_role {
    project = var.region
    roles = [
      "OBS OperateAccess",
      "SWR Admin",
      "CCE Admin",
      # 传输节点数据盘使用自建主密钥加密，节点池创建时 EVS 会以该委托校验 CMK 权限；
      # 缺此项会报 EVS.2264 get Agency's token failed（Invalid cmkid）。
      "KMS CMKFullAccess",
    ]
  }
}

resource "huaweicloud_cce_cluster" "main" {
  name                   = "${var.name_prefix}-cluster"
  flavor_id              = var.cce_control_flavor
  cluster_version        = var.cce_cluster_version != "" ? var.cce_cluster_version : null
  vpc_id                 = huaweicloud_vpc.main.id
  subnet_id              = huaweicloud_vpc_subnet.nodes[0].id
  container_network_type = "overlay_l2"
  container_network_cidr = var.container_network_cidr
  service_network_cidr   = var.service_network_cidr
  security_group_id      = huaweicloud_networking_secgroup.main.id

  tags = {
    managed_by = "terraform"
  }
}

# 可选：用首尾锚定的名称表达式固定节点镜像，避免命中带显卡驱动的变体
data "huaweicloud_images_image" "node" {
  count        = var.use_pinned_node_image ? 1 : 0
  name_regex   = var.node_image_name_regex
  most_recent  = true
  visibility   = "public"
  architecture = "x86_64"
}

# ---------------------------------------------------------------------------
# 列举节点池
# ---------------------------------------------------------------------------

resource "huaweicloud_cce_node_pool" "lister" {
  cluster_id         = huaweicloud_cce_cluster.main.id
  name               = "${var.name_prefix}-lister-pool"
  flavor_id          = var.lister_node_flavor
  os                 = var.node_os
  availability_zone  = var.availability_zones[0]
  subnet_id          = huaweicloud_vpc_subnet.nodes[0].id
  initial_node_count = 1
  password           = var.node_password != "" ? var.node_password : null
  key_pair           = var.node_key_pair != "" ? var.node_key_pair : null

  root_volume {
    size       = var.node_root_volume_size
    volumetype = var.node_root_volume_type
  }

  # CCE 要求非本地盘规格必须声明数据盘（缺省会报
  # CCE_CM.0004 Data volume needed for non-local-disk flavor）。
  # 数据盘容量下限为 100GB（小于该值报 Data volume size must between 100 and 32768），
  # 列举节点池不承载分片缓冲，取下限即可。
  data_volumes {
    size       = max(100, var.node_root_volume_size)
    volumetype = var.node_root_volume_type
  }

  extend_params {
    node_image_id = var.use_pinned_node_image ? data.huaweicloud_images_image.node[0].id : null
  }

  labels = {
    role = "lister"
  }

  lifecycle {
    # 节点数量可能由运维在控制台调整，后续部署不回写
    ignore_changes = [initial_node_count]
  }
}

# ---------------------------------------------------------------------------
# 传输节点池
# ---------------------------------------------------------------------------
# 本实践不启用节点池自动伸缩（未设置伸缩开关与伸缩上下限），
# 节点数以 initial_node_count 固定为 1，副本数由作业侧 worker_replicas 固定。
resource "huaweicloud_cce_node_pool" "worker" {
  cluster_id         = huaweicloud_cce_cluster.main.id
  name               = "${var.name_prefix}-worker-pool"
  flavor_id          = var.worker_node_flavor
  os                 = var.node_os
  availability_zone  = var.availability_zones[1]
  subnet_id          = huaweicloud_vpc_subnet.nodes[1].id
  initial_node_count = 1
  password           = var.node_password != "" ? var.node_password : null
  key_pair           = var.node_key_pair != "" ? var.node_key_pair : null

  root_volume {
    size       = var.node_root_volume_size
    volumetype = var.node_root_volume_type
  }

  # 数据盘用于分片缓冲，内容随节点销毁，静态加密由主密钥保护
  data_volumes {
    size       = var.worker_node_data_volume_size
    volumetype = var.node_root_volume_type
    kms_key_id = huaweicloud_kms_key.volume.id
  }

  extend_params {
    node_image_id = var.use_pinned_node_image ? data.huaweicloud_images_image.node[0].id : null
  }

  labels = {
    role = "transfer"
  }

  lifecycle {
    ignore_changes = [initial_node_count]
  }
}

# 作业命名空间
resource "huaweicloud_cce_namespace" "transfer" {
  cluster_id = huaweicloud_cce_cluster.main.id
  name       = "transfer"

  lifecycle {
    # 命名空间的标注与标签会被集群组件在运行期补写
    ignore_changes = [annotations, labels]
  }
}
