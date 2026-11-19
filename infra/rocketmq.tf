# ============================================================================
# 任务队列：分布式消息服务 RocketMQ 版
# ============================================================================
# 列举组件把对象按批投递到任务主题，传输组件以消费组方式领取。
# 失败重投由消费组的最大重试次数控制，超过次数后进入该消费组的死信主题。

resource "huaweicloud_dms_rocketmq_instance" "main" {
  name              = "${var.name_prefix}-queue"
  engine_version    = var.rocketmq_engine_version
  vpc_id            = huaweicloud_vpc.main.id
  subnet_id         = huaweicloud_vpc_subnet.nodes[1].id
  security_group_id = huaweicloud_networking_secgroup.main.id
  availability_zones = [
    var.availability_zones[1],
  ]

  flavor_id         = var.rocketmq_flavor_id
  storage_spec_code = var.rocketmq_storage_spec_code
  storage_space     = var.rocketmq_storage_space
  broker_num        = var.rocketmq_broker_num

  # 仅在 VPC 内访问，不开公网接入点
  enable_publicip = false

  tags = {
    managed_by = "terraform"
    purpose    = "transfer-task-queue"
  }

  lifecycle {
    # 实例参数在运行期可能由运维在服务端调整，避免下一次部署静默覆盖
    ignore_changes = [configs]
  }
}

# 任务主题：消息体为「源桶 / 目的桶 / 一批对象键」
resource "huaweicloud_dms_rocketmq_topic" "task" {
  name         = "${var.name_prefix}-transfer-task"
  instance_id  = huaweicloud_dms_rocketmq_instance.main.id
  message_type = "NORMAL"
}

# 业务侧死信主题：承载结构非法、无法交给传输组件处理的消息
resource "huaweicloud_dms_rocketmq_topic" "dead_letter" {
  name         = "${var.name_prefix}-transfer-dlq"
  instance_id  = huaweicloud_dms_rocketmq_instance.main.id
  message_type = "NORMAL"
}

# 消费组：retry_max_times 即同一批对象的最大重投次数
resource "huaweicloud_dms_rocketmq_consumer_group" "worker" {
  name            = "${var.name_prefix}-worker-group"
  instance_id     = huaweicloud_dms_rocketmq_instance.main.id
  retry_max_times = var.retry_max_times
  consume_orderly = false
  broadcast       = false
  enabled         = true
}
