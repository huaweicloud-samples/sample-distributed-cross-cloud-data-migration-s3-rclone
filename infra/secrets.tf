# ============================================================================
# 密钥与凭据：数据加密服务 + 凭据管理系统
# ============================================================================
# 三把主密钥分别保护：源端访问凭据、目的端存储桶对象内容、传输节点数据盘内容。
# 每把密钥开启自动轮换，周期 365 天。
# 队列与日志的静态加密由对应服务自身托管，创建接口不接受主密钥入参。
#
# 不声明 pending_days：该参数在 provider 中表示「创建后立即进入待删除日程」。
# 填 "7" 会让密钥建出来就处于 state=4（待删除），节点池引用时报
# Invalid cmkid / EVS.2264 get Agency's token failed，provider 等待就绪也会超时。
# 需要删除密钥时，请单独调用计划删除接口排期。

resource "huaweicloud_kms_key" "secrets" {
  key_alias         = "${var.name_prefix}-secrets-key-v2"
  key_description   = "保护源端存储访问凭据的主密钥"
  key_algorithm     = "AES_256"
  key_usage         = "Encrypt_Decrypt"
  rotation_enabled  = true
  rotation_interval = 365

  tags = {
    managed_by = "terraform"
    purpose    = "credentials"
  }
}

resource "huaweicloud_kms_key" "bucket" {
  key_alias         = "${var.name_prefix}-bucket-key-v2"
  key_description   = "保护目的端存储桶对象内容的主密钥"
  key_algorithm     = "AES_256"
  key_usage         = "Encrypt_Decrypt"
  rotation_enabled  = true
  rotation_interval = 365

  tags = {
    managed_by = "terraform"
    purpose    = "destination-bucket"
  }
}

resource "huaweicloud_kms_key" "volume" {
  key_alias         = "${var.name_prefix}-volume-key-v2"
  key_description   = "保护传输节点数据盘内容的主密钥"
  key_algorithm     = "AES_256"
  key_usage         = "Encrypt_Decrypt"
  rotation_enabled  = true
  rotation_interval = 365

  tags = {
    managed_by = "terraform"
    purpose    = "node-data-volume"
  }
}

# ---------------------------------------------------------------------------
# 凭据管理：源端与目的端访问信息
# ---------------------------------------------------------------------------
# 首次使用凭据管理系统需要建立服务委托（每个账号一次）。
# 若账号已建立该委托，注释掉本资源即可。
#
# secret_type 取 CSMS 支持的凭据托管场景标识，不支持 "COMMON"：
# 实测 COMMON 返回 CSMS.1121「CSMS服务不支持该凭据类型COMMON」，
# RDS-FG（FunctionGraph 侧凭据托管）可正常建立委托。
resource "huaweicloud_csms_agency" "dew" {
  secret_type = "RDS-FG"
}

# 凭据均为占位内容，部署完成后由使用者替换为真实值。
# 传输组件读写两端一律用静态访问密钥，不依赖实例元数据接口的临时凭证：
# 对象存储数据面不接受代维临时凭证，且临时凭证能否被搬运工具读取尚未验证。
# 真实凭据不进入代码仓库，也不进入 Terraform 状态之外的任何文件。
resource "huaweicloud_csms_secret" "source_endpoint" {
  name        = "${var.name_prefix}-source-endpoint"
  secret_type = "COMMON"
  description = "源端对象存储的访问域名"
  kms_key_id  = huaweicloud_kms_key.secrets.id
  secret_text = "<SRC_ENDPOINT>"

  # 真实值由使用者在控制台或命令行写入，后续 apply 不得回写占位内容
  lifecycle {
    ignore_changes = [secret_text]
  }

  depends_on = [huaweicloud_csms_agency.dew]
}

resource "huaweicloud_csms_secret" "source_access_key" {
  name        = "${var.name_prefix}-source-access-key"
  secret_type = "COMMON"
  description = "源端对象存储的访问密钥 ID"
  kms_key_id  = huaweicloud_kms_key.secrets.id
  secret_text = "<SRC_ACCESS_KEY>"

  lifecycle {
    ignore_changes = [secret_text]
  }

  depends_on = [huaweicloud_csms_agency.dew]
}

resource "huaweicloud_csms_secret" "source_secret_key" {
  name        = "${var.name_prefix}-source-secret-key"
  secret_type = "COMMON"
  description = "源端对象存储的私密访问密钥"
  kms_key_id  = huaweicloud_kms_key.secrets.id
  secret_text = "<SRC_SECRET_KEY>"

  lifecycle {
    ignore_changes = [secret_text]
  }

  depends_on = [huaweicloud_csms_agency.dew]
}

# 目的端使用本账号的静态访问密钥写入存储桶。对象存储数据面不接受代维临时凭证，
# 因此目的端凭据同样以静态密钥占位，由使用者写入真实值。
resource "huaweicloud_csms_secret" "dest_access_key" {
  name        = "${var.name_prefix}-dest-access-key"
  secret_type = "COMMON"
  description = "目的端对象存储的访问密钥 ID"
  kms_key_id  = huaweicloud_kms_key.secrets.id
  secret_text = "<HW_ACCESS_KEY>"

  lifecycle {
    ignore_changes = [secret_text]
  }

  depends_on = [huaweicloud_csms_agency.dew]
}

resource "huaweicloud_csms_secret" "dest_secret_key" {
  name        = "${var.name_prefix}-dest-secret-key"
  secret_type = "COMMON"
  description = "目的端对象存储的私密访问密钥"
  kms_key_id  = huaweicloud_kms_key.secrets.id
  secret_text = "<HW_SECRET_KEY>"

  lifecycle {
    ignore_changes = [secret_text]
  }

  depends_on = [huaweicloud_csms_agency.dew]
}
