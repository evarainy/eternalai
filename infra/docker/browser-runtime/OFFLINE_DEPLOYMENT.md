# Browser Runtime v4.2 免费内网部署物料与验收边界

状态：**正式定义已落仓，物料与现场验收未完成**。正式 Dockerfile、Compose、
离线转运清单和真实主应用入口见 [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md)。
本目录原有 `compose.yaml`、`compose.single-run.yaml` 和 `README.md` 仍属固定
synthetic/Jev 试验，不能作为正式启动。尚无构建出的获准镜像、目标服务器实物清单
或全链断网结果。本文不授权实际构建、启动容器、读取凭据或部署。

依据：[Browser Runtime v4.2 免费内网实施计划 r1](https://chatgpt.com/space/page_523d32b1fea881919c79e798b5a48bef)
及[仓库依赖政策](../../../docs/dev/dependency_policy.md)。正式路线使用 Playwright
Python async 与配套 Chromium、本地薄 Provider、既有 Task/授权/PostgreSQL Run/
Verifier；推荐 Linux Engine/Moby + Compose。实际 OS、CPU、容器权限和算力由目标
环境核实，不从开发机推定。JEV 仅是曾获准的临时调试例外，不列入正式依赖或云回退。

## 构建、转运与运行

依赖政策允许在获准的联网开发/构建环境预制镜像，再向内网交付。正式
`Dockerfile.production` 要求带不可变摘要的 Playwright `v1.63.0-noble` 基础镜像，
`pip install uv==0.12.13` 和 `uv sync --frozen --no-dev --no-install-project`
均发生在**构建时**；不能据此声称镜像已可断网构建，也不能把它们带入运行时。
内网运行应只使用事先导入、核对的完整镜像与模型物料，不在线激活、不拉取公网镜像、
不下载浏览器/依赖、不自动转接云服务。若要求**内网从源码构建**，须另取得获准镜像
来源和依赖镜像/离线缓存；仓库依赖政策没有证明这种构建目前可行。

正式 `compose.production.yaml` 将 API 与执行 Worker 分为两个容器，以同一
镜像、factory 和既有 PG Run/租约/身份权威运行，显式传 `--role api|worker`。
API 不启动 worker 或 TTL scheduler，worker 不监听 HTTP；共享同一持久
spool，API 只读、worker 可写，首次空卷先由 worker 初始化私有位置 marker。
引用 operator 外部受控网络、只读 factory/资产配置目录、六个精确 secret 文件、资源限额，
不含试验库/Jev/公网桥或容器内拉取。目标网络策略、工厂代码、Site/Skill 绑定与
真实物料仍须现场提供并验证；文件定义不等于生产就绪。凭据由既有密钥/身份权威
提供，报告只记挂载与访问边界的脱敏核验结果。

## 交付物料记录（实际交付前逐项填证据）

| 物料/条件 | 仓库已确认 | 正式交付尚须取得的证据 |
|---|---|---|
| 目标平台 | r1 推荐 Linux Engine/Moby + Compose；未指定生产机器 | 实际 OS/CPU 架构、容器/沙箱权限、RAM；若模型需要 GPU，再记录实际 GPU 与驱动兼容性 |
| 浏览器工作镜像 | 正式 Dockerfile 要求 Playwright 基础镜像不可变摘要并记录源码/锁摘要标签；尚未构建 | 获准镜像的来源、目标架构、不可变镜像标识、导出文件摘要、导入后标识及对应源码/锁文件版本；不能以标签或旧试验镜像代替 |
| Playwright、Node 与 Chromium | `pyproject.toml`、`uv.lock` 固定 Playwright Python `1.63.0`；现有合成装配从包内定位 Node 和 `/ms-playwright` 浏览器，并校验 Node/Chromium/helper 文件摘要 | 目标 Linux 镜像中实际浏览器发行版本/可执行文件摘要、包内 Node 与 helper 摘要及客户端配套关系；Windows 本地 headless-shell 探针不能代替此项 |
| Python 依赖 | `uv.lock` 是当前锁文件，Dockerfile 按锁安装生产依赖 | 实际镜像内依赖与锁文件/构建来源对应的清单、摘要及所转运第三方物料的许可证记录；锁文件存在不等于镜像已核验 |
| 字体、信任证书、时区和系统依赖 | 当前仓库未给出目标站点所需的完整发行物料 | 按实际站点与目标镜像列出所需字体、证书信任配置、时区数据和系统库，核对来源、版本/摘要及适用许可；不把未知项写成已满足 |
| PostgreSQL、授权与 Profile | 既有技术 Run 和授权/凭据权威需复用；试验 Compose 仅指向固定测试环境 | 正式服务的受控连接、现有 schema 兼容性、身份/Profile 归属与安全挂载的非敏感证明；不得用试验库或试验密钥路径替代 |
| 主 Runtime 与模型 | 项目合同为 vLLM raw JSON；首条确定性浏览器读取不要求目标选择模型 | 主 Runtime 的内网服务/模型物料、版本、许可证和可用算力；目标选择模型只在该路径启用前记录通过验收的版本、权重、Tokenizer、推理代码及许可 |
| API/执行与文件存储 | 正式 Compose 已定义分离角色、同源持久 spool 与读写挂载；定义尚未启动 | 实际角色进程/监听、PG 跨进程接纳和取消、权限与首次 marker 顺序、TTL 存储归属及总资源预算 |
| DNS、日志与文件链 | r1 要求全链断网验收 | 实际服务解析、日志落点、文件处理与更新通道的断外网观察；不能只凭单个浏览器进程启动证明 |

许可证逐项对应实际发行物料。Playwright 的许可证不能替代 Chromium、系统包、
字体或模型的许可核查。表中待取得项不以开发机、历史试验或产品文档自动填绿。

## 最小验收顺序与当前缺口

1. 确认获准目标平台与受控只读业务范围，固定实际镜像、浏览器、依赖、字体、证书、
   主 Runtime/模型及其许可；在转运前后比对制品摘要，并记录镜像与源码/锁文件绑定。
2. 在该平台隔离公网后冷启动已导入的服务和必要依赖，核对没有运行时拉取、安装、
   激活、浏览器下载或云回退；记录实际 OS/架构、镜像和浏览器版本及资源边界。
3. 经既有受控入口完成获准的读取、独立结果核验和资源清理；同时观察主模型、
   必要的 Decision 分支、DNS、日志、文件链与失败路径。业务效果未知时保留隔离，
   不靠重放或换 Provider 宣称恢复。
4. 将命令、退出状态、脱敏的实际结果、制品标识和未执行原因绑定同一候选保存。
   本文没有目标机和业务授权事实；正式操作形状见独立 runbook，实际执行须另获授权。

目标服务器 OS/CPU/容器权限/算力、实际物料及已授权真实业务路径尚未取得；这些
条件阻断实际配置与验收结论，不阻断仓库内通用定义。目前未构建或转运正式镜像，
未执行断网冷启动，也未证明实际业务、模型、日志和文件全链离线。
