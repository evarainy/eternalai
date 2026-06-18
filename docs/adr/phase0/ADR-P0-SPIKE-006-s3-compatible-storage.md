# ADR-P0-SPIKE-006 — S3-compatible Object Storage Candidate Evaluation

- task_id: P0-SPIKE-006
- date: 2026-05-10
- status: accepted
- decision_makers: [Phase 0 Spike]

## 1. Context

Phase 1 及后续阶段可能需要对象存储能力（文件上传/下载、附件管理、知识库文档存储等）。主蓝图要求 S3-compatible Object Storage 抽象，不锁死具体产品。Phase 0 Spike 阶段需要评估候选方案的许可证、部署、权限、运维风险，为 Phase 1 决策提供依据。

**重要声明**：本次评估基于公开资料（官方文档、GitHub 仓库、Docker Hub、官方博客、社区讨论），未连接任何真实存储服务或生产系统。所有无法从公开资料确认的结论标记为 `needs_vendor_confirmation` 或 `needs_test_environment`。

## 2. Candidate Comparison

| 候选 | 类型 | 许可证 | S3 API 兼容性 | 评估深度 | evidence_tier |
|------|------|--------|-------------|----------|--------------|
| MinIO Community Server | 自托管 S3-compatible | GNU AGPLv3 | 完整兼容 | 详细（首选候选） | public_docs |
| NAS S3 Gateway (群晖/威联通等) | 设备内置 S3 网关 | 厂商专有 | 部分兼容 | 概述（secondary） | needs_vendor_confirmation |
| 企业对象存储 (华为 OBS / 阿里 OSS S3 兼容模式) | 企业级对象存储 | 厂商专有 | S3 兼容模式 | 概述（secondary） | needs_vendor_confirmation |
| 云对象存储 (AWS S3 / MinIO Gateway) | 云服务 | 云厂商条款 | 原生 S3 | 概述（secondary） | public_docs |
| Ceph RADOS Gateway | 自托管分布式 | LGPL 2.1 | 兼容 | 概述（secondary） | public_docs |
| Garage | 自托管轻量级 | AGPLv3 | 兼容 | 概述（secondary） | public_docs |

## 3. MinIO Deep Dive

### 3.1 license_current_state

- MinIO Community Server（`minio/minio` on GitHub / `minio/minio` on Docker Hub）当前版本按 **GNU AGPLv3** 评估。
- MinIO 自 ~2021 年前后已从 Apache 2.0 转向 GNU AGPLv3。历史 Apache 2.0 许可证信息仅为背景，不作为当前许可结论。
- 2024 年起，部分企业功能（如全球复制、高级审计等）已从 Community 版移至 Commercial AIStor / MinIO Software License。
- 具体版本号对应的许可证应以 [官方 GitHub](https://github.com/minio/minio)、[Docker Hub](https://hub.docker.com/r/minio/minio)、[官方博客](https://blog.min.io/) 为准。
- evidence_tier: public_docs

### 3.2 license_risk

- GNU AGPLv3 要求：如果通过网络向用户提供服务（包括内部员工），修改后的源码必须向用户公开。
- 仅使用未修改的 MinIO 二进制/Docker 镜像与自定义配置，是否触发 AGPLv3 义务，取决于"衍生作品"的法律定义，需要法务确认。
- **不得自行下法律结论**。以下为需要法务/合规审查的具体问题：
  - 在内网部署未修改的 MinIO 容器是否构成 AGPLv3 意义下的"分发"或"传递"？
  - 通过 S3 API 交互是否触发 AGPLv3 的"远程网络交互"条款？
  - 自定义配置文件、启动脚本是否构成"修改"？

### 3.3 AGPLv3 compliance risk

- AGPLv3 compliance 是**法律/合规审查风险**，本 spike 不作定论。
- Phase 1 启用 MinIO 的前提必须包含：**legal/compliance review confirms AGPLv3 acceptability, or commercial license / alternative storage path is acceptable.**
- 如果法务判定 AGPLv3 不可接受，替代路径：
  1. 评估 MinIO Commercial AIStor 许可证
  2. 采用 NAS S3 Gateway
  3. 采用企业对象存储（华为 OBS / 阿里 OSS）
  4. 采用其他许可协议可接受的 S3-compatible 方案

### 3.4 commercial_license_or_alternative_required

- AIStor / MinIO Software License 需单独评估定价、功能边界和采购流程。
- 如果 Community AGPLv3 不可接受且商业许可成本不合理，应转向其他候选。
- evidence_tier: needs_vendor_confirmation（商业许可定价与功能边界）

### 3.5 deployment_mode

- 单机 Docker Compose 部署：
  ```yaml
  # 示例（非生产配置，凭证必须通过环境变量或 secrets manager 注入）
  services:
    minio:
      image: minio/minio:latest
      ports:
        - "9000:9000"
        - "9001:9001"
      environment:
        MINIO_ROOT_USER: ${MINIO_ROOT_USER}        # set via .env or secrets manager
        MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}  # set via .env or secrets manager
      command: server /data --console-address ":9001"
      volumes:
        - minio_data:/data
  volumes:
    minio_data:
  ```
  **注意**：示例中不包含任何可直接使用的默认凭证。实际部署时必须通过 `.env` 文件或 secrets manager 注入 `MINIO_ROOT_USER` 和 `MINIO_ROOT_PASSWORD`。
- 集群模式（Erasure Coding）：MinIO 支持多节点分布式部署，最少 4 节点。Phase 0 spike 不验证集群模式。
- 内网部署：MinIO 为纯自托管方案，不要求外部网络连接。
- evidence_tier: public_docs

### 3.6 backup_strategy

- `mc mirror`：MinIO 客户端工具，支持增量同步到另一个 MinIO 实例或本地目录。
- Bucket versioning：MinIO 支持对象版本控制，可用于软删除恢复。
- 文件系统级备份：可对 MinIO data volume 进行快照/备份。
- evidence_tier: public_docs（备份方案需在实际部署中验证可靠性）

### 3.7 permission_model

- IAM policy：MinIO 内置 IAM 系统，支持用户、组、策略。
- Bucket policy：类似 AWS S3 的 bucket-level 访问控制。
- Service Account：可创建子账号并绑定特定策略。
- 与企业 AD/LDAP 集成：MinIO 支持 OpenID Connect 和 LDAP/AD 身份联合（部分功能已移至 Commercial 版）。
- Phase 0 spike 不验证实际权限配置。
- evidence_tier: public_docs

### 3.8 operational_risks

| 风险 | 说明 | 缓解 |
|------|------|------|
| 单点故障 | 单机部署无冗余 | Phase 1 若启用，评估 Erasure Coding 多节点 |
| 数据丢失 | 单磁盘模式无纠删码 | 定期备份 + volume 快照 |
| 许可证变更 | AGPLv3 合规风险（见 3.2/3.3） | 法务确认 + 备选方案 |
| 企业功能锁定 | 全球复制、高级审计等已移至 Commercial | Phase 1 需求分析确认 Community 是否足够 |
| 监控集成 | MinIO 提供 Prometheus metrics endpoint | 集成 OpenTelemetry（与项目架构一致） |
| 升级风险 | 版本升级可能引入 breaking changes | 锁定版本 + 测试后再升级 |

## 4. Alternatives (Secondary Comparison)

### 4.1 NAS S3 Gateway

- 群晖（Synology）/ 威联通（QNAP）等 NAS 设备提供内置 S3 兼容网关。
- 优势：已有的 NAS 设备可直接启用，无需额外服务。
- 劣势：S3 API 兼容性可能不完整；厂商专有许可证；性能受限于 NAS 硬件。
- evidence_tier: needs_vendor_confirmation

### 4.2 企业对象存储

- 华为 OBS / 阿里 OSS 均提供 S3 兼容 API 模式。
- 优势：企业级 SLA、运维托管。
- 劣势：可能需要云环境或额外采购；许可证为厂商专有。
- evidence_tier: needs_vendor_confirmation

### 4.3 Ceph RADOS Gateway

- Ceph 是开源分布式存储系统，RGW 提供 S3 兼容 API。
- 许可证：LGPL 2.1（对内网部署更友好）。
- 劣势：部署和运维复杂度远高于 MinIO；单机场景性价比低。
- evidence_tier: public_docs

### 4.4 Garage

- 轻量级自托管 S3-compatible 存储。
- 许可证：AGPLv3（与 MinIO 相同的许可证约束）。
- 优势：轻量、Rust 编写。
- 劣势：社区规模小、企业级功能有限。
- evidence_tier: public_docs

## 5. Spike Evidence

### 5.1 Spike Scripts

| 文件 | 用途 |
|------|------|
| `experiments/phase0/s3_storage/minio_put_get_delete_test.py` | 最小 put/get/delete 测试脚本 |
| `experiments/phase0/s3_storage/s3_test_helpers.py` | S3 client factory 和 bucket 生命周期辅助函数 |

### 5.2 Static Check Results

- Python `ast.parse` / `py_compile` 静态检查：见 Task Record `tests_run` 字段。
- 真实 S3 操作：**needs_test_environment**（无 Docker / MinIO 实例）。
- boto3 依赖：**needs_internal_mirror_confirmation**（未确认内部 PyPI 镜像是否包含 boto3）。

### 5.3 MinIO Docker Compose Deployment Test

- **needs_test_environment**：当前无 Docker 运行环境，未执行实际部署。
- Docker Compose 配置示例见 §3.5。

## 6. Phase 1 Recommendation

**建议：有条件启用 MinIO Community 作为 Phase 1 对象存储方案。**

前提条件：

1. **Legal/compliance review confirms AGPLv3 acceptability**, or commercial license / alternative storage path is acceptable.
2. boto3 确认在内网 PyPI 镜像中可用。
3. 开发/测试环境提供 Docker 运行能力。
4. Phase 1 需求分析确认 Community 版功能是否满足（不依赖已移至 Commercial 的企业功能）。

替代路径（若 AGPLv3 不可接受）：

1. 评估 MinIO Commercial AIStor 许可证。
2. 采用 NAS S3 Gateway（需确认 S3 API 兼容性）。
3. 采用企业对象存储 S3 兼容模式。
4. 采用 Ceph RGW（LGPL 2.1，但运维复杂度高）。

## 7. spike_code_disposition

- `experiments/phase0/s3_storage/` 下所有文件为 spike-only。
- 废弃或沉淀为 spike-only；不进入 `app/` 正式模块。
- 若 Phase 1 需要复用，由独立任务审查、加固后迁移至 `tests/utils/` 或正式模块。

## 8. needs_test_environment

- 真实 MinIO Docker 实例（用于 put/get/delete 实际执行）。
- Docker Compose 部署验证。
- 权限模型实际配置与验证。

## 9. needs_internal_mirror_confirmation

- boto3 是否在内网 PyPI 镜像中可用。
- botocore 是否在内网 PyPI 镜像中可用（boto3 的传递依赖）。

## 10. needs_vendor_confirmation

- NAS S3 Gateway 的具体型号与 S3 API 兼容性范围。
- 企业对象存储（华为 OBS / 阿里 OSS）S3 兼容模式的具体限制。
- MinIO Commercial AIStor 的定价与功能边界。
- 企业 AD/LDAP 与 MinIO 联合认证的具体配置。
