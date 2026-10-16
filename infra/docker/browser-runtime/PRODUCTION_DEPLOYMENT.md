# Browser Runtime 正式免费内网镜像与离线交付

本页定义**代码侧可复现的路径**；镜像、Site、模型、许可及目标机器尚无实物验收。
`Dockerfile.production`、`Dockerfile.production.dockerignore` 和
`compose.production.yaml` 独立于本目录既有的 synthetic/Jev 试验文件。
正式镜像通过 `scripts.run_browser_production` 读取既有六项配置 secret，
原样转交 `scripts.run_browser_registered_server --role api|worker`。API 和执行
Worker 是两个容器，复用同一 PostgreSQL Run/租约、身份、授权与核验权威。

## 1. 构建与来源绑定

受控构建机先确认源码候选 SHA、`uv.lock` SHA256、实际目标 Linux/amd64
（或经批准的目标架构）与所选基础镜像的不可变摘要和许可。`Dockerfile.production`
要求 `mcr.microsoft.com/playwright/python:v1.63.0-noble@sha256:<64 hex>`，
安装固定 `uv==0.12.13` 并用 `uv sync --frozen --no-dev --no-install-project`
安装主应用生产依赖；镜像内不运行 pip/uv/浏览器下载。构建仍可能联网，
只有预先导入全部基础镜像与依赖缓存且实测成功，才能称内网源码构建。

在仓库根目录、已设定下列变量的受控构建环境执行（这些命令尚未在本任务执行）：

```sh
docker build --file infra/docker/browser-runtime/Dockerfile.production --build-arg PLAYWRIGHT_BASE_IMAGE="$PLAYWRIGHT_BASE_IMAGE" --build-arg SOURCE_COMMIT="$SOURCE_COMMIT" --build-arg UV_LOCK_SHA256="$UV_LOCK_SHA256" --tag "$BROWSER_RUNTIME_IMAGE" .
docker image inspect "$BROWSER_RUNTIME_IMAGE"
docker save --output "$BROWSER_IMAGE_ARCHIVE" "$BROWSER_RUNTIME_IMAGE"
```

`SOURCE_COMMIT` 必须是实际候选的 40 位 Git SHA；`UV_LOCK_SHA256` 必须是
该源码 `uv.lock` 的 64 位 SHA256。Dockerfile 在构建时检查这两个形状、
基础镜像摘要形状及锁文件实值，并将 commit/锁摘要写入镜像标签。
交付前另核对镜像检查结果、`docker save` 文件摘要、包内 Playwright/Node/
Chromium 与字体/证书/时区/系统库的实际版本、第三方许可证和源代码对应关系。
标签只是构建声明，不能替代受控构建记录和镜像层审计。

### 正式入口源码与依赖清单

`Dockerfile.production.dockerignore` 是逐文件精确的构建上下文清单；`**`
默认排除；每个 `!目录/` 后紧接 `目录/*`，重新排除其子树，再逐个
放行所列文件，避免目录例外连带放行 synthetic/凭据或新文件。Dockerfile
只复制该清单中的 `app/`、两个启动脚本及下列 Decision 合同文件：

| 必需源码 | 入口和传递依赖 |
|---|---|
| `scripts/run_browser_production.py` | secret wrapper，延迟导入 registered 启动脚本，原样转交角色参数 |
| `scripts/run_browser_registered_server.py` | 仓内 ProductionComponents、local_installation；API 角色另使用主应用入口，worker 使用同一 registered 装配和 supervisor |
| `services/decision/__init__.py`、`manifest.py` | `local_installation.py` 无条件导入 `PinnedCheckpoint`；manifest 只使用 Python 标准库、锁定的 Pydantic 和已列入镜像的 `app.browser_skill.models` |
| `pyproject.toml`、`uv.lock` | `uv sync --frozen --no-dev --no-install-project` 安装依赖，故仓内模块必须另由 COPY 纳入，不能依赖项目安装隐式补源码 |

`services/` 是 Python namespace，没有仓内根 `__init__.py`；镜像的
`PYTHONPATH=/opt/eternalai` 提供该 namespace。只包含两个 Decision 合同文件，
不包含 `api.py`、`service.py`、`laya_backend.py`、模型权重或 wheelhouse；
独立本地 Decision 服务的实物仍按下文模型分支交付。operator bundle 只供应
受审 Site/工厂输入，不补平台源码。源码闭包测试以两个启动脚本和 API 角色延迟加载的 `app.main` 为入口，
按 AST 检查 import-time 的仓内传递导入及对应 COPY 目的路径；惰性的历史 synthetic 方法与受审 operator
工厂另按实际路径验收。已有共用类型所需 `fixed_synthetic_seed.py` 仍在精确
清单内，正式入口不选择试验安装或 Jev；没有新增放行 synthetic/Jev 文件。
这份清单和 AST 证据不能代替构建后在实际镜像内的导入检查。

## 2. 离线转运包

`scripts/verify_browser_production_bundle.py` 只读 `bundle-root` 中 manifest
精确列出的文件，检查大小、SHA256、路径和链接；不读取凭据，不 import 主应用，
不启动 Docker，也不证明镜像内部内容或许可结论。manifest 采用
`browser.production.bundle.v1` JSON，字段恰为 `schema`、`source_commit`
（40 位小写 SHA）、`activation`（`deterministic` 或 `decision`）和 `artifacts`。
每项字段恰为 `role`、`path`（bundle 根内 ASCII 相对路径）、`sha256`
（64 位小写）与 `size_bytes`（正整数）。不得用占位文件填满角色。

| 模式 | 必须各有一份的角色 |
|---|---|
| 两种模式 | `image_archive`、`uv_lock`、`browser_asset_manifest`、`license_inventory`、`site_registration`、`main_runtime_manifest` |
| 两种模式 | `operator_factory_file` 至少一份，逐个列出实际可 import 的受审模块文件 |
| `decision` | 另有 `decision_checkpoint_archive`、`decision_checkpoint_manifest`、`decision_wheelhouse_archive`、`decision_wheel_lock`、`decision_notices` 各一份 |

`browser_asset_manifest` 是实际 `LocalOfflineManifest`，由可信工厂按
`app.infra.browser.deployment_manifest` 验证 digest、Node/Chromium/
Playwright/helper/font/certificate 文件及当前平台。`site_registration` 必须
绑定同一已注册 Site/Skill、授权与本地 Decision 来源；转运脚本只校验其文件字节，
不执行这些语义。`decision` 模式还需按 `services/decision/wheelhouse.py`
核对全部 35 个或该目标锁定的 wheel 及实际 notice，再按
`services/decision/offline.py` 核对 checkpoint 和已安装依赖。
这两份 Decision archive 还须在受控目标解包后对每个实际文件运行 M2 的
精确哈希和许可校验；转运 archive 摘要不能代替内部文件验收。
`deterministic` 不把未校准的目标选择模型当成已上线，但主 Runtime vLLM
与其离线物料仍必须有真实验收。许可清单逐项对照镜像、浏览器、依赖、
字体、证书和模型实际发行物料；本仓库不附带已完成许可审批。

在转运前后各运行一次下面的只读校验，并保存两次摘要与退出码：

```sh
python -m scripts.verify_browser_production_bundle --bundle-root "$BROWSER_BUNDLE_ROOT" --manifest "$BROWSER_BUNDLE_MANIFEST" --expected-source-commit "$SOURCE_COMMIT" --expected-uv-lock-sha256 "$UV_LOCK_SHA256"
```

## 3. 已导入镜像的正式 Compose 入口

`compose.production.yaml` 定义两个服务，共用同一已导入镜像、受审 factory
引用和六个 secret 来源，均使用 `pull_policy: never`、无 `build`、无宿主
公开端口：

| 服务 | registered 参数 | 执行与挂载边界 |
|---|---|---|
| `browser-api` | `--enable --factory <module:function> --role api --host 0.0.0.0 --port 8000` | 现有 HTTP/鉴权/CSRF；不启动 BrowserWorkerSupervisor、浏览器资源或 TTL scheduler；spool 只读 |
| `browser-worker` | `--enable --factory <同一 module:function> --role worker` | 不运行 uvicorn/HTTP；在同一权威校验的安装 context 内运行既有 supervisor、PG 扫描/租约/Executor/Verifier 与 cleanup；spool 可写 |

`--role` 缺省为 `api`，Compose 始终显式指定角色。worker 不依赖 API 进程
内存；接纳、取消和执行协调使用现有 PG Run 与租约权威，不新增服务身份、
业务 API 或执行队列。相同镜像与同一工厂只共享定义和持久权威，各容器
具有独立进程和生命周期。API 监听只在上述容器参数明确启用，现有鉴权与
CSRF 保持适用；`expose` 不发布宿主端口。
Compose 不创建数据库、Redis、主 Runtime、Decision 服务或业务站点，
它们必须是已批准且可达的内网依赖。

operator 在**仓库外**提供：已审计的本地 Python factory 与 Site/Skill/
浏览器资产配置目录 `BROWSER_OPERATOR_BUNDLE_ROOT`（只读挂载）。该 factory
是接收仓内 `ProductionComponents` 的 async context manager，yield 单个
`RegisteredProductionInputs` 或其 tuple；它只供应真实的已签 Site、binding、
deployment、provider pool、proof/grants/cleanup authority、keyrings，以及路径
启用时需要的 Decision provider/checkpoint、文件/审查等 Site 专属组件。
`scripts.run_browser_registered_server` 调用仓内
`build_registered_production_installation`，从现有主应用组件绑定 session、
registry、binder、policy、trace 和 worker 所需权威，并只在 chat 模式接入现有 Runtime
LLM；structured 模式不装配它。不要求 operator
重写这些通用接线。operator 另提供独立的
`BROWSER_ARTIFACT_ROOT`（同一持久 spool，仅 worker 的受授权文件链写入）；六个 Docker Compose secret
源文件；内部 DNS、已存在的受控网络、Chromium seccomp profile 与经测量的
CPU/RAM/PID/shm/tmpfs 限额。`scripts.run_browser_production` 只从固定
`/run/secrets` 文件名读六项已有配置 secret 到进程内的现有环境配置接口，
拒绝预置的同名环境变量、非普通文件、空值、超长和多行值，不回显内容。
业务/账号/Profile 凭据继续由现有密钥/授权权威提供；factory 只持有所需
受控引用和生命周期，不写进镜像、仓库、
Compose、命令参数或报告。容器进程内环境仍含配置 secret，需按现有部署
进程隔离控制；不得通过 `docker compose config` 输出其解析值。

配置还明确要求 OA live、受控 Profile/租户、内网 OA 与内网 vLLM。
基础 Compose 不注入 Decision 模型三元值；这**不授予**无模型权限。仅当
已签 Site 的 `decision_budget.max_candidates == 1` 且不含 exploration，
生产安装校验才允许无模型的确定性路径；其余 Site 必须绑定真实本地
Decision provider/checkpoint。启用后一分支时叠加
`compose.production-decision.yaml`，由 operator 提供真实、完整的 origin、
request model 与 pinned deployment model，且按 Decision 离线清单验收。
可信 factory 必须提供实际 Site/Skill/资产、身份与授权输入；缺失时仓内
装配和最终运行门禁 fail closed。挂载一个 Python 文件或 JSON 并不自动完成
业务注册，也不存在通用 JSON 代替 login/verifier/身份权威。Operator 网络须通过
防火墙与 DNS 策略禁止外网，仅放行审批站点和必要内网依赖；Compose
只指定外部网络，不证明此策略，也不把 Playwright route 当防火墙。

### 持久文件卷与首次启动

两角色必须将**同一个** `BROWSER_ARTIFACT_ROOT` 绑定到
`/var/lib/eternalai/browser-artifacts`；API 为只读，worker 为读写。
此目录须在启动前由现场批准的运维流程建立，属于镜像实际 `pwuser` UID，
权限 `0700`，已有制品与私有 spool marker 按实现要求保持 `0600`。
Compose 的 `create_host_path: false` 不自动建宿主目录，也不执行 chown。
`/run/browser-operator` 两侧只读；`/tmp` 是各容器独立限额 tmpfs，不能
用于持久 spool。root 文件系统只读、非 root、无 Docker socket 与宿主通用
目录挂载；worker 单独配置受限 shm，API 与 worker 分别配置实测资源预算，
目标机总预算须覆盖二者并发。

持久 spool 的 storage_scope 由已有文件实现的私有 marker 绑定物理位置，
PG/MAC 事件仍是元数据与授权权威；不由 Compose/env 新造范围或标识。
工厂向 `RegisteredProductionInputs.file_artifact_root` 传入
`Path("/var/lib/eternalai/browser-artifacts")`，与 `file_artifacts` 对象互斥；
仓内 builder 按启动角色初始化/读取 `.browser-file-storage-scope-v1` 并绑定
现有 PG 文件权威，operator 无须补写存储或范围接线。
两个工厂实例必须使用相同 spool 根、已有 marker、签名密钥上下文与相同
注册 Site/Skill/版本，API 只读校验已存在 marker。首次空卷须先启动 worker，
待其完成 marker/装配初始化后再启动 API；提前启动 API 应 fail closed，
不能降级创建另一位置标识。仅容器 `started` 不证明初始化完成。后续重启
应复用同一目录与 marker；不同 spool 根不可共用该位置标识，也不能扫描
或确认删除另一存储范围的事件。现有文件 scheduler 只在 worker 角色启动。

获准运行时，先执行 `docker compose ... up -d --no-build --pull never
browser-worker`，确认 worker 初始化与上述权限后，再执行同形命令启动
`browser-api`。此处命令仅定义顺序，本窗口没有执行。实际转移、启动与
权限调整均须使用现场获准流程；私有 marker 内容不进入日志或报告。

目标环境完成文件、导入镜像、凭据、网络、配置与受控业务批准后，按
`compose.production.yaml` 显式启动；正式部署仍须另获授权。离线验收
用已导入镜像、`docker compose ... up --no-build --pull never`，逐项观察
主模型、Decision（若启用）、DNS、日志、文件链、鉴权/隔离、Run 终态、
浏览器进程终止与资源限额。暂停新 Run、结算或隔离在途 Run 后，才用
上一组**镜像与匹配配置**回退；UNKNOWN 不重放，历史结果/Profile 不删除。

上述构建、打包、启动、断网和真实业务操作均未在本任务执行；具体机器、
镜像/模型文件、notice、内网依赖与获准 Site/账号仍须由现场提供和验收。
