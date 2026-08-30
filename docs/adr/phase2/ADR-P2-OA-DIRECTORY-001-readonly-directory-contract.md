# ADR-P2-OA-DIRECTORY-001 — OA 只读组织目录合同与用途边界

- 状态：Accepted
- 日期：2026-08-30
- 承接任务：`P2-GOV-SYNC-036`
- 影响任务：`P2-OA-ORGANIZATION-DIRECTORY-001`

## 背景

`P2-OA-ORGANIZATION-DIRECTORY-001` 原描述含「**部门授权查询能力**」。该表述与两条已生效裁决冲突：

- 2026-08-20：本项目采用**每用户自身凭证**访问 OA，**从 OA 能取得的数据范围以该用户在 OA 中的权限为准**。
- 2026-08-20：OA 是每用户自己的账号，**用户自己的凭证天然限定了权限范围**。

即：**权限由 OA 自身执行，本系统不另判**（身份权限透传）。在本系统内再建一套部门授权判定，
会产生与 OA 并行的第二套授权模型；两者一旦不一致，风险由本系统承担。

同一批裁决还写明：若确需从 OA 取得部门级数据，只有两条路——该用户在 OA 中本就具备部门查询权限
（属个案），或引入具备部门权限的账号（**与「不用共享服务账号」的既有约束冲突，须单独裁决**）。

## 决定

### 一、用途边界

| | 内容 |
|---|---|
| **保留** | OA 只读组织目录合同；本地组织镜像。用途限于**本系统的登录身份展示**与**后续聊天功能的收件人选择** |
| **删除** | 「部门授权查询能力」。与身份权限透传冲突，不在本项目实现 |
| **收窄** | 「可信 Principal 部门上下文」**只作展示**，不得作为任何授权、可见性或路由判据 |

**不可协商**：本系统**不实现**任何基于组织关系的授权判定。审批权、下一处理人、可见范围
一律由 OA 自身决定，本系统只透传用户凭证并呈现 OA 的返回。

### 二、已固化的接口结构

以下结构来自一次只读页面采集，**已在本 ADR 固化，后续实现不必再回头接触采集件**。

**记录边界（硬约束）**：只记录端点路径、参数名、响应字段名与类型。
**主机地址、真实姓名、组织节点 ID 与名称、任何令牌或会话标识一律不记录。**

#### 调用链

```
① GET  /api/hrm/base/getHrmSearchTree      → 部门/分部树
② POST /api/hrm/search/getHrmSearchResult  → 返回 sessionkey（会话级查询令牌）
③ POST /api/ec/dev/table/datas             → 人员行，按页取
④ POST /api/ec/dev/table/counts            → 记录总数
```

②产出的 `sessionkey` 作为③④的 `dataKey` 传入。

#### ① `GET /api/hrm/base/getHrmSearchTree`

请求参数名：`__random__`、`id`、`isLoadSubDepartment`、`isVirtual`、`keyword`、`type`、`virtualCompanyid`

响应结构：

```text
datas: list[object]
  canClick: bool      canceled: bool     icon: str        id: str
  isParent: bool      isVirtual: str     name: str        pid: str
  psubcompanyid: str  selected: bool     title: str       type: str
```

**该树只含组织节点，不含人员属性**（无电话、邮箱、职务等字段）。无分页参数。

#### ② `POST /api/hrm/search/getHrmSearchResult`

编码 `application/x-www-form-urlencoded`。

请求参数名：`departmentid`、`tabkey`、`showAllLevel`、`virtualtype`、`resourcename`、
`manager`、`subcompany`、`department`、`telephone`、`mobile`、`mobilecall`、`jobtitle`

响应：`sessionkey: str`。

⚠️ **`sessionkey` 与 `dataKey` 按凭证对待**——不得进入 ResponseEnvelope、Trace、日志或 fixture
（`AGENTS.md` 不可协商规则 4）。

#### ③ `POST /api/ec/dev/table/datas`

编码 `application/x-www-form-urlencoded`。请求参数名：`dataKey`、`current`、`sortParams`。

响应顶层：`datas`、`columns`、`ops`、`rootMap`、`pageSize`、`pageAutoWrap`、`isSts`、
`haveCheck`、`expandOperate`、`timeJson`、`status`。

**`datas[]` 人员行字段**（均为 `str`）：

| 类别 | 字段 |
|---|---|
| 身份 | `id`、`workcode`、`lastname`、`sex`、`status`、`accounttype` |
| 联系 | `mobile`、`telephone`、`mobilecall`、`fax`、`email`、`workroom` |
| 组织 | `departmentid`、`subcompanyid1`、`orgid`、`locationid`、`managerid`、`assistantid`、`belongto` |
| 职务 | `jobtitle`、`jobactivity`、`jobactivitydesc`、`joblevel`、`jobcall`、`jobGroupId` |
| 其他 | `dsporder`、`systemlanguage`、`textfield1`、`textfield2`、`randomField0`、`randomFieldId` |

⚠️ **每个字段都有配对的 `<字段>span` 变体**，是 e-cology 的 HTML 渲染值。
**建模只取原始字段，`*span` 全部排除**——冗余，且更易夹带内容。

`columns[]` 字段：`orderkey`、`labelid`、`_index`、`dataIndex`、`display`、`dbField`、
`orders`、`fromExport`、`oldWidth`、`title`、`tablename`、`transMethod`、
`transMethodOther`、`transMethodOther2`、`from`。
其中 `dataIndex` → `title` 是**字段名到中文列标题**的映射，可用于 pydantic 模型的 `description`。

`rootMap` 字段：`tabletype`、`isEncryptShare`、`pagesize`、`pageUid`、`pageId`、`exportRight`。

**分页**：页大小见 `rootMap.pagesize` 与顶层 `pageSize`；`current` 为页码。

#### ④ `POST /api/ec/dev/table/counts`

请求参数名：`dataKey`。响应：`count: int`、`status: bool`。

### 三、实现时仍须验证的两点

1. **`managerid` 的语义未定**——从字段名无法判断它是「人的直属上级」还是「部门负责人」。
   原描述中的「唯一主负责人」需实现时以真实数据验证。
2. **完整性判定**：`count` 与实际取回行数必须一致，否则视为快照不完整。

**fail-closed 边界（沿用原定）**：只能取得姓名、或无法确认完整性时，**停止实现并报告，
不得创建猜测式映射**。第 1 点确认不了同样停手。

## 后果

- `P2-OA-ORGANIZATION-DIRECTORY-001` 范围缩小，不再包含授权判定；仍属**机会层**，不阻塞 P2 收口。
- 采集件不再是该棒的前置——接口结构已固化于本 ADR。采集件本体永不进仓库
  （2026-08-20「HAR 清洗脚本进仓库，被清洗的 HAR 素材不进」）。
- 本 ADR 记录的结构可供后续用途复用：登录身份展示、聊天收件人选择，以及任何**不涉及授权判定**的场景。
