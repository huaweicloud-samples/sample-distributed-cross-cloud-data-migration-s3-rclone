# ============================================================================
# 镜像仓库：为两个组件建立组织与仓库
# ============================================================================
# 组件镜像由使用者本地构建后推送到这里，再被集群拉取。
# 仓库保持私有，集群侧以拉取凭据访问（见部署指导书）。
# 销毁时先删仓库再删组织（terraform destroy -target 可指定仓库先行）。

resource "huaweicloud_swr_organization" "main" {
  name = var.name_prefix
}

resource "huaweicloud_swr_repository" "lister" {
  name         = var.swr_lister_image
  organization = huaweicloud_swr_organization.main.name
  description  = "对象列举组件镜像"
  is_public    = false
}

resource "huaweicloud_swr_repository" "worker" {
  name         = var.swr_worker_image
  organization = huaweicloud_swr_organization.main.name
  description  = "对象传输组件镜像"
  is_public    = false
}
