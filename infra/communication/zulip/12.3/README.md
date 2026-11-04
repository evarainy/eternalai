# Zulip 12.3 同页送核导航增量

本目录保存 `P2-IM-FILE-DELIVERY-001` 直接需要的 IM 侧源码补丁及固定输入说明；保留到主控指定清理，不自动删除。它扩充原 `web/src/host_bridge.ts`，继续使用现有 `click_handlers.ts` 捕获链接和同一个 `send`，不增加第二桥或业务执行入口。

## 固定输入与应用

- 来源树：`E:/code/eternalai-im-infra/src/zulip`，Zulip **12.3**；读取 HEAD 为 `997ce02875ca5bff009eee169e1310eff402ec48`。原树已有菜单、路由、注销薄补丁，不能把本补丁直接应用到没有这些前置的干净上游。
- 精确输入：`web/src/host_bridge.ts`，SHA256 `5a21e2ac95c0d21ec084035f1fa5526e98b4772622a1142b1f8ffbfe19a4d581`。按源文件哈希识别实际前置，不能只凭 Git HEAD 判断未提交补丁状态。
- 精确输出（尚未填写部署配置）：SHA256 `b10ef45de0e574be3e11edafe7d813e131ccd77539b8db89fec048c98de75fc0`。
- 获准的 IM 构建 writer 在自己的源码副本根先核输入哈希，再运行 `git apply --unidiff-zero --check <本目录>/0004-delivery-navigation.patch`；检查通过后才按该次授权以 `git apply --unidiff-zero <本目录>/0004-delivery-navigation.patch` 应用。本补丁使用零行上下文，必须先核精确输入哈希。当前任务只在仓库外临时副本核对可应用性，不修改原 IM 树，不构建或部署。

原 `HostEvent` 调用保持在同一个模块内规范化：选消息的数字 ID 转为字符串、私聊对象去掉当前用户并排序，`conversation_ref` 按现有 Zulip reader 的不透明 JSON 合同编码；注销事件只传空 payload。统一输出 `protocol: eternalai.im-bridge`、`version: 2`、固定 provider/instance、type 和 payload，不保留旧 v1 信封回退。

只识别固定宿主 origin 下的 `/messages`、`/work-objects/<UUID>` 和 `/messages?delivery=<UUID>`；送核链接转换为 `eternalai:navigate` 的 `route: delivery`、`object_ref`。既有捕获器据 `route_link` 的 true 阻止另开标签，宿主继续核 origin/source/身份代次；点击仅导航，发送与回执仍需本人明确动作。

## 部署前置与证据边界

`config.parent_origin`、`config.im_instance_id`、`config.realm_id` 默认为空，须由获准构建 writer 填入与 EternalAI 后端相同的固定部署值；provider 为 `zulip`。origin 必须是精确 HTTPS origin，不带路径或尾斜杠；不得从消息载荷选择目标 origin。配置未齐时桥保持关闭。本补丁不创建 Bot、不配置凭据、不改变认证或业务权限。

V1 的 IM-origin 身份/注销辅助页及其代理安装仍是现场前置；本补丁不替代该辅助页，也不把补丁可应用性检查当成 Zulip 构建、已安装、真实同页导航或真实送核验收。应用后需在获准环境验证：甲乙正确身份、嵌入消息卡点击仍在宿主同页打开原 delivery、退出/切号后旧窗口不推进动作。

协议依据为 `EXECUTION_PLAN.md` §3.4 与 `v2plan/V2_PLAN.md` §5.1；浏览器消息使用固定 targetOrigin，参见 [Window.postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage)。
