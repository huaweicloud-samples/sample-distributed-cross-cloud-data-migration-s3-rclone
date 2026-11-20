# ============================================================================
# 网络：VPC / 子网 / 安全组 / 出网通道
# ============================================================================
# 传输组件需要主动访问公网上的源端对象存储，出网能力由 NAT 网关提供：
# 容器引擎的节点由节点池动态创建，实例标识在编排阶段不可预知，
# 无法为每个节点单独绑定弹性公网 IP，因此采用「1 个 NAT 网关 + 1 个共享公网 IP」
# 的集中出网方式，SNAT 规则覆盖三个子网。
#
# 出方向默认全通由平台自动注入，不再重复创建（重复创建会报冲突）。
# 创建 NAT 网关时平台会自动向默认路由表写入 0.0.0.0/0 指向 NAT 的路由，
# 因此这里不显式创建默认路由。

resource "huaweicloud_vpc" "main" {
  name = "${var.name_prefix}-vpc"
  cidr = var.vpc_cidr
  tags = {
    managed_by = "terraform"
    component  = "network"
  }
}

resource "huaweicloud_vpc_subnet" "nodes" {
  count = length(var.subnet_cidrs)

  name              = "${var.name_prefix}-node-subnet-${count.index + 1}"
  vpc_id            = huaweicloud_vpc.main.id
  cidr              = var.subnet_cidrs[count.index]
  gateway_ip        = cidrhost(var.subnet_cidrs[count.index], 1)
  availability_zone = var.availability_zones[count.index]

  # 保持平台内置内网域名解析服务，容器与节点才可正常解析云上服务地址
  dns_list = ["100.125.1.1", "100.125.3.1"]

  tags = {
    managed_by = "terraform"
    component  = "network"
  }
}

# ---------------------------------------------------------------------------
# 安全组：只允许出方向，不开任何入方向端口
# ---------------------------------------------------------------------------

resource "huaweicloud_networking_secgroup" "main" {
  name        = "${var.name_prefix}-sg"
  description = "跨云对象同步专用安全组，无入方向规则"
}

# 出方向不放行任何入方向端口，且不需要显式建出方向放行规则：
# 平台在建组时已自动插入一条全通出方向规则，重复创建会报冲突。
# rclone 访问源端与目的端所需的出网能力由该默认规则提供。

# ---------------------------------------------------------------------------
# 出网通道
# ---------------------------------------------------------------------------

resource "huaweicloud_vpc_eip" "egress" {
  name = "${var.name_prefix}-egress-eip"

  publicip {
    type = "5_bgp"
  }

  bandwidth {
    name        = "${var.name_prefix}-egress-bw"
    share_type  = "PER"
    size        = var.nat_eip_bandwidth_size
    charge_mode = "traffic"
  }

  tags = {
    managed_by = "terraform"
    component  = "egress"
  }
}

resource "huaweicloud_nat_gateway" "egress" {
  name      = "${var.name_prefix}-egress-nat"
  vpc_id    = huaweicloud_vpc.main.id
  subnet_id = huaweicloud_vpc_subnet.nodes[0].id
  spec      = var.nat_spec

  tags = {
    managed_by = "terraform"
    component  = "egress"
  }
}

resource "huaweicloud_nat_snat_rule" "nodes" {
  count = length(huaweicloud_vpc_subnet.nodes)

  nat_gateway_id = huaweicloud_nat_gateway.egress.id
  floating_ip_id = huaweicloud_vpc_eip.egress.id
  subnet_id      = huaweicloud_vpc_subnet.nodes[count.index].id
}
