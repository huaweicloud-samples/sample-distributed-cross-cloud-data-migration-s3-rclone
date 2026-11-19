# sample-distributed-cross-cloud-data-migration-s3-rclone

[![Status](https://img.shields.io/badge/Status-Incubating-blue)]()
[![Huawei Cloud](https://img.shields.io/badge/Huawei%20Cloud-Samples-red)]()
[![Scenario](https://img.shields.io/badge/Scenario-multi-cloud-success)](https://3ms.huawei.com/docs/docinfo/1300901382590492672?bookstackId=866760814559879168&gid=3591759&l=zh-cn&documentkind=&attachmentIdx=5)

This practice moves large volumes of object data into Huawei Cloud Object Storage Service (OBS) using a distributed, resumable and idempotent job pipeline. The source side only needs to expose an S3-compatible object storage interface: a service domain name, an access key ID and a secret access key are enough to read the object listing and the object payloads through virtual-hosted addressing. The destination is always the regional OBS endpoint `obs.<region>.myhuaweicloud.com`.

## Overview

This repository is created from the huaweicloud-samples automated repository request workflow.

## Getting Started

Add setup, deployment, and verification steps here.

## Contributing

Please use pull requests and follow the repository review rules.

## License

This project is licensed under the MIT-0 license.

## Maintainers

CODEOWNERS: @Ferguson2211

## Feedback

Please use GitHub Issues: https://github.com/huaweicloud-samples/sample-distributed-cross-cloud-data-migration-s3-rclone/issues
