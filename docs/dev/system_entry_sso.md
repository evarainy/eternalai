# 应用页 OA / 财务免登入口

应用页的账号设置继续保存本人的账号密码。OA 绑定沿用本人身份验证；U8 新绑定先执行真实
登录，失败不保存。已保存的历史 U8 账号显示「已保存」，点击时仍会现场验证，不能据此认为
旧账号已通过验证。

点击卡片后先开空窗，再向 CSRF 保护的本人入口请求一次性导航 URL。失败关闭空窗并提示；
切换工作台身份后不导航。URL 不进入 Query 缓存、持久化存储、Trace 或 ResponseEnvelope。

## 运行配置

主 API 沿用现有启动方式，新增以下非凭据配置入口：

- U8_BASE_URL：默认 http://34.64.8.70:8188。
- U8_RSA_PUBLIC_KEY_PATH：运行机器上的 RSA 公钥文件路径。使用当前 U8 客户端的协议公钥，
  未配置时 U8 绑定及免登不可用；错误或无法读取时显示服务不可用。
- OA_BRIDGE_PUBLIC_BASE：浏览器可访问的桥 origin，如 https://oa-entry.example，
  不含子路径。未配置时 OA 卡保留原有直链并明确显示「免登入口未配置，打开原系统」。
- OA_BRIDGE_LISTEN：仅独立桥使用，默认 127.0.0.1:18083。

独立桥与主 API 使用相同的现有 REDIS_URL、ETERNALAI_CREDENTIAL_ENCRYPTION_KEY_B64，
并读取 OA_BASE_URL、CSRF_ALLOWED_ORIGINS。不增加数据库表或迁移。
Redis 需支持 GETDEL（6.2+）。依赖在构建阶段安装，不在内网运行时下载安装。

在已获授权的运行配置准备完毕后，独立进程入口为：

    uv run python -m app.system_entry.oa_bridge

该入口只启动桥，不导入 app.main，不启动轮询、作业或工作台后台任务。
HTTP 访问日志关闭；业务日志只记阶段和状态码，不记请求路径、查询参数或凭据。
在桥前加网关时，网关同样应关闭入口访问日志或屏蔽 /oa-enter/ 后的票据，
不能在审计或异常记录中保留完整 URL。
no-referrer 仅用于票据入口和桥自身错误响应；普通 OA 响应保留原 Referrer-Policy，
避免破坏 OA 表单的 Origin/Referer。上游故障与未知拒绝码按服务不可用呈现，不猜测密码错误。

## OA 的地址与会话

桥必须使用不同于工作台的浏览器主机名，可以同一台服务器、同一个 IP 配不同名字。
Cookie 不按端口隔离，所以只换端口仍会混用。当前代码不修改 hosts、DNS、系统配置或部署。

OA 登录复用现有不持久化 verifier，并核对工作台本人及 OA subject。
新的浏览器会话不写入后台代持会话。Cookie 加密存 Redis，随机票据 60 秒过期且原子消费；
Redis 失败不给成功入口。

桥只转发固定 OA 上游，双向 Cookie 限定为协议资料中的七个名称。入口 Cookie 为 host-only、
Path=/，只有 ecology_JSessionid 标记 HttpOnly，其余保持 OA 前端可读；代理响应保留
续期、删除与 HttpOnly/Secure/SameSite 属性，移除上游 Domain 并规范 Path，并清理新票据中缺失的旧 OA Cookie。
不安全请求先核对浏览器原始 Origin（无 Origin 时用 Referer），再改写至 OA。
同上游的绝对 Location 映射回桥。每个代理请求使用独立客户端，不共享 CookieJar。
保留 OA 自身的页面响应和 X-Frame-Options；新窗口打开，不内嵌。

无自动重登、业务重试、全站正文替换或 WebSocket 转发。
只有实际验收证明缺项阻断当前入口时再定点处理。

## 验收状态与边界

静态检查、跨厂商 Review 与真实登录是不同证据。真实验收需用户当次授权登录/凭据使用，
并由用户目视确认 OA / U8 落地及本人身份；不截图或读取登录后的业务页面内容。
须核对浏览器打开及退出前后后台 OA 会话仍有效，独立 CookieJar 不能证明 OA 服务端不会互踢。
U8 原生 URL token 方案沿用已批准计划；token 不写入日志、报告或应用持久化存储。
当前 U8 第一步通过 RSA 公钥恢复会话密钥，后续沿用 AES-ECB / PKCS7 请求和响应；
旧示例的私钥解密方式与当前客户端不符。产品授权及本人登录结果以实际验收为准。
