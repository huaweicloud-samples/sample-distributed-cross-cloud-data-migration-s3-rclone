# ============================================================================
# 目的端对象存储
# ============================================================================
# 传输组件把源端对象逐个写入本桶。桶级策略强制 HTTPS 访问。

resource "huaweicloud_obs_bucket" "dest" {
  bucket        = var.dest_bucket_name
  acl           = "private"
  storage_class = "STANDARD"
  multi_az      = false

  # 服务端加密：使用本实践自建的主密钥，开启年度轮换
  encryption    = true
  sse_algorithm = "kms"
  kms_key_id    = huaweicloud_kms_key.bucket.id
  force_destroy = var.force_destroy_bucket

  tags = {
    managed_by = "terraform"
    purpose    = "transfer-destination"
  }
}

# 只允许通过 HTTPS 访问本桶。
# 策略为对象存储原生格式：Resource 直接写桶名、Action 写裸名、
# Principal 必须是对象、Version 固定为 2008-10-17、条件值必须是字符串数组。
resource "huaweicloud_obs_bucket_policy" "dest_tls_only" {
  bucket        = huaweicloud_obs_bucket.dest.id
  policy_format = "obs"

  policy = jsonencode({
    Version = "2008-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = { ID = ["*"] }
        Action    = ["*"]
        Resource  = [huaweicloud_obs_bucket.dest.bucket, "${huaweicloud_obs_bucket.dest.bucket}/*"]
        Condition = { Bool = { SecureTransport = ["false"] } }
      }
    ]
  })
}
