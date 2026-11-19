# ============================================================================
# 分布式跨云对象数据同步实践 —— Terraform 入口
# ============================================================================

terraform {
  required_version = ">= 1.5"

  required_providers {
    huaweicloud = {
      source  = "huaweicloud/huaweicloud"
      version = "~> 1.96"
    }
  }

  backend "local" {}
}

# 凭据一律由执行环境变量提供，不写入本文件：
#   export HW_ACCESS_KEY=<你的访问密钥 ID>
#   export HW_SECRET_KEY=<你的访问密钥>
provider "huaweicloud" {
  region = var.region
}
