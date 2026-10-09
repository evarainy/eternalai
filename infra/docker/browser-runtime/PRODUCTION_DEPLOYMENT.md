# Browser Runtime 正式免费内网镜像与离线交付

本页定义**代码侧可复现的路径**；镜像、Site、模型、许可及目标机器尚无实物验收。
`Dockerfile.production`、`Dockerfile.production.dockerignore` 和
`compose.production.yaml` 独立于本目录既有的 synthetic/Jev 试验文件。
正式入口是主应用 `scripts.run_browser_registered_server`，同一应用 lifespan
管理 API 与 BrowserWorkerSupervisor，不创建第二套 Task/授权/Run 服务。

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

`compose.production.yaml` 只有一个 `browser-runtime` 服务；使用镜像引用、
`pull_policy: never`、无 `build`、无公网端口，显式运行 `--enable --factory
<operator module:function> --host 0.0.0.0 --port 8000`。入口默认为 loopback；
只有这份容器配置主动启用网络监听。API 仍走主应用现有鉴权与 CSRF。
Compose 不创建数据库、Redis、主 Runtime、Decision 服务或业务站点；
它们必须是已批准且可达的内网依赖。

operator 在**仓库外**提供：已审计的本地 Python factory 与 Site/Skill/
浏览器资产配置目录 `BROWSER_OPERATOR_BUNDLE_ROOT`（只读挂载）；独立的
`BROWSER_ARTIFACT_ROOT`（仅受授权文件链写入）；六个 Docker Compose secret
源文件；内部 DNS、已存在的受控网络、Chromium seccomp profile 与经测量的
CPU/RAM/PID/shm/tmpfs 限额。`scripts.run_browser_production` 只从固定
`/run/secrets` 文件名读六项已有配置 secret 到进程内的现有环境配置接口，
拒绝预置的同名环境变量、非普通文件、空值、超长和多行值，不回显内容。
业务/账号/Profile
凭据继续由可信 factory 和现有密钥/授权权威提供；不写进镜像、仓库、
Compose、命令参数或报告。容器进程内环境仍含配置 secret，需按现有部署
进程隔离控制；不得通过 `docker compose config` 输出其解析值。

配置还明确要求 OA live、受控 Profile/租户、内网 OA 与内网 vLLM。
基础 Compose 不注入 Decision 模型三元值；这**不授予**无模型权限。仅当
已签 Site 的 `decision_budget.max_candidates == 1` 且不含 exploration，
生产安装校验才允许无模型的确定性路径；其余 Site 必须绑定真实本地
Decision provider/checkpoint。启用后一分支时叠加
`compose.production-decision.yaml`，由 operator 提供真实、完整的 origin、
request model 与 pinned deployment model，且按 Decision 离线清单验收。
可信 factory 和正式入口须完成实际 Site/Skill/资产注册接线后才能启动。
挂载一个 Python 文件或 JSON 并不自动完成注册。Operator 网络须通过
防火墙与 DNS 策略禁止外网，仅放行审批站点和必要内网依赖；Compose
只指定外部网络，不证明此策略，也不把 Playwright route 当防火墙。

目标环境完成文件、导入镜像、凭据、网络、配置与受控业务批准后，按
`compose.production.yaml` 显式启动；正式部署仍须另获授权。离线验收
用已导入镜像、`docker compose ... up --no-build --pull never`，逐项观察
主模型、Decision（若启用）、DNS、日志、文件链、鉴权/隔离、Run 终态、
浏览器进程终止与资源限额。暂停新 Run、结算或隔离在途 Run 后，才用
上一组**镜像与匹配配置**回退；UNKNOWN 不重放，历史结果/Profile 不删除。

上述构建、打包、启动、断网和真实业务操作均未在本任务执行；具体机器、
镜像/模型文件、notice、内网依赖与获准 Site/账号仍须由现场提供和验收。
