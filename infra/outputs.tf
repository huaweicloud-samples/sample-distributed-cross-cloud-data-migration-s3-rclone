# ============================================================================
# 输出
# ============================================================================

output "region" {
  description = "部署区域"
  value       = var.region
}

output "vpc_id" {
  description = "虚拟私有云编号"
  value       = huaweicloud_vpc.main.id
}

output "node_subnet_ids" {
  description = "节点子网编号列表"
  value       = [for s in huaweicloud_vpc_subnet.nodes : s.id]
}

output "security_group_id" {
  description = "安全组编号"
  value       = huaweicloud_networking_secgroup.main.id
}

output "egress_public_ip" {
  description = "出网公网地址"
  value       = huaweicloud_vpc_eip.egress.address
}

output "cluster_id" {
  description = "容器集群编号"
  value       = huaweicloud_cce_cluster.main.id
}

output "lister_node_pool_id" {
  description = "列举节点池编号"
  value       = huaweicloud_cce_node_pool.lister.id
}

output "worker_node_pool_id" {
  description = "传输节点池编号"
  value       = huaweicloud_cce_node_pool.worker.id
}

output "namespace" {
  description = "作业命名空间"
  value       = huaweicloud_cce_namespace.transfer.name
}

output "dest_bucket" {
  description = "目的端存储桶名"
  value       = huaweicloud_obs_bucket.dest.bucket
}

output "dest_bucket_domain" {
  description = "目的端存储桶访问域名"
  value       = huaweicloud_obs_bucket.dest.bucket_domain_name
}

output "obs_endpoint" {
  description = "对象存储服务域名"
  value       = "obs.${var.region}.myhuaweicloud.com"
}

output "rocketmq_instance_id" {
  description = "任务队列实例编号"
  value       = huaweicloud_dms_rocketmq_instance.main.id
}

output "rocketmq_namesrv_address" {
  description = "队列接入地址，作业配置需要"
  value       = huaweicloud_dms_rocketmq_instance.main.namesrv_address
}

output "rocketmq_broker_address" {
  description = "Broker 接入地址"
  value       = huaweicloud_dms_rocketmq_instance.main.broker_address
}

output "task_topic" {
  description = "任务主题名"
  value       = huaweicloud_dms_rocketmq_topic.task.name
}

output "dead_letter_topic" {
  description = "业务侧死信主题名"
  value       = huaweicloud_dms_rocketmq_topic.dead_letter.name
}

output "native_dead_letter_topic" {
  description = "达到重投上限后由队列自动投递的死信主题名"
  value       = "%DLQ%${huaweicloud_dms_rocketmq_consumer_group.worker.name}"
}

output "worker_consumer_group" {
  description = "传输组件消费组名"
  value       = huaweicloud_dms_rocketmq_consumer_group.worker.name
}

output "worker_replicas" {
  description = "传输副本数（固定值，需调整时修改该变量并重新提交作业）"
  value       = var.worker_replicas
}

output "swr_login_server" {
  description = "镜像仓库登录地址"
  value       = huaweicloud_swr_organization.main.login_server
}

output "lister_image" {
  description = "列举组件镜像地址"
  value       = "${huaweicloud_swr_organization.main.login_server}/${var.name_prefix}/${var.swr_lister_image}:${var.image_tag}"
}

output "worker_image" {
  description = "传输组件镜像地址"
  value       = "${huaweicloud_swr_organization.main.login_server}/${var.name_prefix}/${var.swr_worker_image}:${var.image_tag}"
}

output "log_group_id" {
  description = "日志组编号"
  value       = huaweicloud_lts_group.main.id
}

output "lister_log_stream" {
  description = "列举组件日志流名"
  value       = huaweicloud_lts_stream.lister.stream_name
}

output "worker_log_stream" {
  description = "传输组件日志流名"
  value       = huaweicloud_lts_stream.worker.stream_name
}

output "credential_names" {
  description = "需要写入真实值的凭据名称（占位内容，值不落盘）"
  value = [
    huaweicloud_csms_secret.source_endpoint.name,
    huaweicloud_csms_secret.source_access_key.name,
    huaweicloud_csms_secret.source_secret_key.name,
    huaweicloud_csms_secret.dest_access_key.name,
    huaweicloud_csms_secret.dest_secret_key.name,
  ]
}

# 与作业提交脚本衔接的就绪提示：变量与命令一一对应
output "submit_job_command" {
  description = "启动一次同步作业的命令"
  value = join(" ", [
    "SRC_BUCKET=<源端桶名>",
    "DST_BUCKET=${huaweicloud_obs_bucket.dest.bucket}",
    "PREFIX=<源端键前缀，可留空>",
    "ROCKETMQ_NAMESRV=${huaweicloud_dms_rocketmq_instance.main.namesrv_address}",
    "TASK_TOPIC=${huaweicloud_dms_rocketmq_topic.task.name}",
    "CONSUMER_GROUP=${huaweicloud_dms_rocketmq_consumer_group.worker.name}",
    "DEAD_LETTER_TOPIC=${huaweicloud_dms_rocketmq_topic.dead_letter.name}",
    "LISTER_IMAGE=${huaweicloud_swr_organization.main.login_server}/${var.name_prefix}/${var.swr_lister_image}:${var.image_tag}",
    "WORKER_IMAGE=${huaweicloud_swr_organization.main.login_server}/${var.name_prefix}/${var.swr_worker_image}:${var.image_tag}",
    "OBS_ENDPOINT=obs.${var.region}.myhuaweicloud.com",
    "bash src/jobs/submit_transfer_job.sh",
  ])
}
