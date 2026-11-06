# 中文、浅色与助手频道订阅

`configure.py` 是 Zulip 12.3 管理 shell 脚本，只维护此仓库源；离线部署包在打包时原样复制到 `deploy/configure.py`。它调用原生 action，更新缓存、事件和审计，不需要 API key。执行会写指定组织的设置或订阅，须先取得目标环境及成员变更授权；本棒仅在合成 `im-acc-zulip` 验收。

## 参数

参数通过进程环境传入，不读取 `.env`。所有 ID 均为 Zulip 数字 ID，列表用逗号分隔、重复 ID 去重；对象必须已经存在于指定 realm。操作者须为获授权的服务器管理员，脚本按 ID 取对象，不模拟 REST 权限预检，也不创建用户、Bot 或频道。

| 参数 | 用途 |
|---|---|
| `ACTION` | `ui` 或 `subscribe` |
| `REALM_ID` | 目标组织 ID |
| `ACTOR_ID` | 同组织的操作人 ID，记录在原生审计中 |
| `HUMAN_IDS` | `ui` 可选：明确要覆盖偏好的现有人类用户 ID；省略只改组织默认 |
| `BOT_IDS` | `subscribe`：明确要添加的助手 Bot ID 列表 |
| `CHANNEL_IDS` | `subscribe`：明确要加入的现有频道 ID 列表 |

`ui` 将组织语言设为 `zh-hans`、新用户默认配色设为 `3`（浅色），并修改指定用户的这两项偏好。重复执行仅修改不同值；再次指定用户会覆盖其后来主动更改的偏好。网页新用户注册可能优先采用浏览器语言，不能保证所有未来用户自动中文；开通后可显式执行 `HUMAN_IDS`。本棒未实测新账号注册。

`subscribe` 只追加指定 Bot/频道组合，恢复已退订项，不取消其他订阅、不改变频道权限。输出仅含 ID、目标设置值、`changed` / `already_subscribed` 计数；缺失对象直接报 ORM 异常。参数不设默认目标；运行前核准账号及频道 ID。

## 两种调用形态

以下为 Bash，变量由运维明确设置；`ui` 用 `HUMAN_IDS`，`subscribe` 用 `BOT_IDS` 和 `CHANNEL_IDS`。省略不适用参数即可。

```bash
# 已获准的部署环境：从部署包执行，不依赖仓库 checkout。
COMPOSE_DISABLE_ENV_FILE=1 docker compose -f deploy/compose.yaml exec -T -u zulip \
  -w /home/zulip/deployments/current \
  -e ACTION=ui -e REALM_ID="$REALM_ID" -e ACTOR_ID="$ACTOR_ID" \
  -e HUMAN_IDS="$HUMAN_IDS" zulip ./manage.py shell < deploy/configure.py

# 本机合成验收：同一脚本，明确容器与动作。
docker exec -i -u zulip -w /home/zulip/deployments/current \
  -e ACTION=subscribe -e REALM_ID="$REALM_ID" -e ACTOR_ID="$ACTOR_ID" \
  -e BOT_IDS="$BOT_IDS" -e CHANNEL_IDS="$CHANNEL_IDS" \
  im-acc-zulip-web-1 ./manage.py shell < deploy/configure.py
```

## 新开助手或新建频道

新开助手后，对需要它参与的频道执行 `subscribe`。新建频道要用“委托 @本人助手 @目标助手 问题”前，先执行 `subscribe` 添加 A/B，或由有权用户在原生“频道设置 → 订阅者”中选择并添加 A/B。两位助手都加入后再发一条新委托；补订阅不会重放旧消息。

Zulip 12.3 的“@未订阅”提示对 Bot 不出现，默认频道对新 Bot 不生效，不能依靠这两项自动接入。日常成员管理只执行订阅步骤，无需重跑人类偏好。

依据：[12.3 管理 shell](https://zulip.readthedocs.io/en/12.3/production/management-commands.html)、[频道订阅 API](https://zulip.com/api/subscribe)；具体 action 签名及 Bot 例外已对照 12.3 固定源码。
