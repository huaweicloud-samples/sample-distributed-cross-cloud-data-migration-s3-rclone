# ============================================================================
# 日志服务：日志组、日志流、容器标准输出采集
# ============================================================================
# 两个组件都只把日志写到标准输出，由集群侧采集器投递到对应日志流，
# 保留天数与 var.log_retention_days 一致。

resource "huaweicloud_lts_group" "main" {
  group_name  = "${var.name_prefix}-logs"
  ttl_in_days = var.log_retention_days

  tags = {
    managed_by = "terraform"
  }
}

resource "huaweicloud_lts_stream" "lister" {
  group_id    = huaweicloud_lts_group.main.id
  stream_name = "${var.name_prefix}-lister"
  ttl_in_days = var.log_retention_days
}

resource "huaweicloud_lts_stream" "worker" {
  group_id    = huaweicloud_lts_group.main.id
  stream_name = "${var.name_prefix}-worker"
  ttl_in_days = var.log_retention_days
}

resource "huaweicloud_lts_cce_access" "lister" {
  count         = var.enable_lts_cce_access ? 1 : 0
  name          = "${var.name_prefix}-lister-access"
  cluster_id    = huaweicloud_cce_cluster.main.id
  log_group_id  = huaweicloud_lts_group.main.id
  log_stream_id = huaweicloud_lts_stream.lister.id

  access_config {
    path_type        = "logs"
    stdout           = true
    stderr           = true
    name_space_regex = "transfer"
    pod_name_regex   = ".*lister.*"

    single_log_format {
      mode = "raw_text"
    }
  }
}

resource "huaweicloud_lts_cce_access" "worker" {
  count         = var.enable_lts_cce_access ? 1 : 0
  name          = "${var.name_prefix}-worker-access"
  cluster_id    = huaweicloud_cce_cluster.main.id
  log_group_id  = huaweicloud_lts_group.main.id
  log_stream_id = huaweicloud_lts_stream.worker.id

  access_config {
    path_type        = "logs"
    stdout           = true
    stderr           = true
    name_space_regex = "transfer"
    pod_name_regex   = ".*worker.*"

    single_log_format {
      mode = "raw_text"
    }
  }
}
