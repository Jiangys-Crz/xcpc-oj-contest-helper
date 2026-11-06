# 历史 VP 数据 API 调用文档

## 1. 用途与数据范围

本接口供协会官网等外部系统查询某个协会用户在某场原站比赛中的历史 VP 情况。

- **用户唯一查询标识为协会官网 ID `externalId`**，即协会外部登录接口 `data.userId`，统一按字符串传递。不能使用邮箱、账号、学号或 VP 系统内部用户 UUID 代替。
- **比赛查询标识为原站比赛 URL `contestUrl`**，例如 `https://qoj.ac/contest/3617`，不是本系统的 VP 房间地址，也不是外榜地址。
- 仅查询已同步到云端、已经结束的 `vp` 记录；不返回 `live`、独立补题 `bt`、预约中、进行中、失败或取消的 VP。
- 同一用户多次 VP 同一原站比赛时，每次历史记录均独立返回，按创建时间倒序、记录 ID 倒序排列。可以通过 `limit=1` 获取最新的一次已结束记录。
- 包含个人 VP，以及用户属于当时参赛队伍的组队 VP。队员后续退出队伍、队伍改名或取消房间报名，不改变已有历史的成员归属。组队记录中的成绩是整队成绩。
- 数据来自终端同步的最近快照，本接口不会向原站抓取数据，也不会自动生成缺失的参赛记录。

本文中的线上基础地址为 `https://vp.cwnupaa.com`。新功能需部署后才能在线使用；本地联调时替换为本地服务地址。

## 2. 用户绑定

用户通过 VP 系统的协会账号登录入口成功登录后，服务器将协会返回的 `userId` 保存到 `users.external_id`，与本系统用户和既有历史记录绑定。后续登录优先按此 ID 更新昵称、账号等资料，用户修改邮箱或账号后仍保留同一关联。

在“我的账号”可以查看“协会官网 ID”。登录响应以及 `GET /api/auth/me` 的当前用户资料也包含 `externalId`；未绑定协会用户的本地管理员此字段为 `null`。普通用户搜索或队伍公开资料不额外公开该字段。

现有已登录过的协会用户保留数据库中原有的绑定，无需重建历史。尚未绑定的用户应先登录一次；本接口不会用邮箱或账号猜测绑定。

## 3. 添加与使用 API Key

管理员进入“网站设置 → 历史 VP 数据 API Key”，输入名称，点击“添加 API Key”。

- 系统生成高强度随机密钥，完整值仅在创建成功时返回并显示一次，请立即复制保存。
- 数据库只存储密钥的 SHA-256 摘要和展示前缀。密钥列表不能取回完整值。
- 每个密钥可只读查询所有已绑定用户的已结束 VP；仅交给可信的服务端调用方，不要放入网页前端代码。
- 设置页支持查看创建时间、最近调用时间，以及撤销密钥。撤销立即生效且不能恢复，需要时创建新密钥。
- 最多同时保留 100 个有效密钥。密钥没有自动到期时间。

推荐请求头：

```http
Authorization: Bearer <API_KEY>
```

也可以使用 `X-API-Key: <API_KEY>`。两者同时提供时必须相同。不支持 URL 参数传密钥，不支持用网页登录 Cookie 或登录会话 token 替代 API Key。API Key 不能用于登录、修改设置、同步比赛或读取打印源码等其他接口。

每个有效密钥每分钟最多请求 **120 次**，不同密钥独立计数。超限返回 `429` 和 `Retry-After: 60`；至少等待 60 秒后再试。计数保存在当前服务进程内，进程重启会清空；多实例部署时各实例独立计数。

## 4. 查询接口

```http
GET /api/external/vp-history
```

### 查询参数

| 参数 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| `externalId` | 是 | string | 协会官网 ID，即外部登录返回的 `data.userId`，最长 64 字符 |
| `contestUrl` | 是 | string | 原站比赛 URL，最长 500 字符 |
| `limit` | 否 | integer | 每页 1–100 条，默认 20 |
| `offset` | 否 | integer | 跳过的记录条数，0–1000000，默认 0 |

参数区分大小写；拒绝重复参数及其他未定义参数，包括 `email`。

`contestUrl` 采用项目已有的原站链接校验规则，接受 HTTPS 的以下比赛页面：

| 平台 | URL 示例 |
| --- | --- |
| Codeforces | `https://codeforces.com/contest/1234` |
| Gym | `https://codeforces.com/gym/105000` |
| QOJ | `https://qoj.ac/contest/3617` |
| Universal Cup | `https://contest.ucup.ac/contest/1234` |
| 协会 OJ | `https://oj.cwnupaa.com/contest/0123456789abcdef01234567`，也支持 `/d/域名/contest/比赛ID` |
| 牛客 | `https://ac.nowcoder.com/acm/contest/12345` |

查询会去掉末尾 `/` 和主机名开头的 `www.` 后精确匹配云端的原站比赛 URL；不同平台、不同比赛 ID 不会合并。链接不能包含查询参数、片段、用户名、密码或自定义端口。URL 校验支持上述平台，不代表该用户或该平台一定已有 `vp` 历史；没有匹配记录时返回空列表。

### cURL 示例

先在当前终端设置 `VP_HISTORY_API_KEY` 为设置页创建的密钥，再执行：

```bash
curl --fail-with-body --get 'https://vp.cwnupaa.com/api/external/vp-history' \
  --header "Authorization: Bearer ${VP_HISTORY_API_KEY}" \
  --data-urlencode 'externalId=101' \
  --data-urlencode 'contestUrl=https://qoj.ac/contest/3617' \
  --data-urlencode 'limit=20' \
  --data-urlencode 'offset=0'
```

### Python 示例

```python
import os
import requests

response = requests.get(
    "https://vp.cwnupaa.com/api/external/vp-history",
    headers={"Authorization": f"Bearer {os.environ['VP_HISTORY_API_KEY']}"},
    params={
        "externalId": "101",
        "contestUrl": "https://qoj.ac/contest/3617",
        "limit": 20,
        "offset": 0,
    },
    timeout=20,
)
response.raise_for_status()
data = response.json()["data"]
for record in data["records"]:
    print(record["id"], record["rank"], record["solved"], record["penalty"])
```

获取下一页时，使用当前 `offset + limit` 作为新 `offset`，直至超过 `total`。分页期间如有新的历史同步，倒序列表的位置可能变化，建议调用方按记录 `id` 去重。

## 5. 成功响应

HTTP `200`，统一外层结构为 `{"data": ...}`。下面展示主要字段，其余历史元数据可一并返回：

```json
{
  "data": {
    "user": {
      "id": "00000000-0000-4000-8000-000000000001",
      "externalId": "101",
      "useraccount": "student",
      "username": "昵称",
      "realname": "张同学"
    },
    "contestUrl": "https://qoj.ac/contest/3617",
    "total": 1,
    "limit": 20,
    "offset": 0,
    "records": [
      {
        "id": "00000000-0000-4000-8000-000000000002",
        "contestName": "训练赛",
        "contestUrl": "https://qoj.ac/contest/3617",
        "platform": "qoj",
        "mode": "vp",
        "participantType": "individual",
        "participantTitle": "昵称",
        "participantSubtitle": "张同学",
        "participantLabel": "昵称-张同学",
        "participantId": null,
        "roomId": null,
        "rank": "87",
        "rankSource": "original",
        "solved": 1,
        "penalty": 20,
        "points": null,
        "scoring": "ICPC",
        "prize": null,
        "prizeSource": "unavailable",
        "externalRankUrl": null,
        "problems": ["A", "B"],
        "cells": {
          "A": {"tries": 1, "pending": 0, "solved": true, "practiceSolved": false, "solveTime": 20},
          "B": {"tries": 0, "pending": 0, "solved": false, "practiceSolved": false}
        },
        "submissions": [
          {
            "id": "12345",
            "problem": "A",
            "submittedAt": "2026-11-01T01:20:00.000Z",
            "verdict": "CORRECT",
            "practice": false,
            "language": "other",
            "url": "https://qoj.ac/submission/12345"
          }
        ],
        "startedAt": "2026-11-01T01:00:00.000Z",
        "endedAt": "2026-11-01T06:00:00.000Z",
        "status": "complete",
        "createdAt": "2026-11-01T00:57:00.000Z",
        "updatedAt": "2026-11-01T06:00:10.000Z",
        "rules": {"duration": 18000, "scoring": "ICPC"},
        "frozen": false
      }
    ],
    "serverNow": "2026-11-06T01:00:00.000Z"
  }
}
```

### 字段约定

| 字段 | 说明 |
| --- | --- |
| `user.id` | VP 系统内部用户 UUID，不是查询参数中的协会官网 ID |
| `user.externalId` | 已绑定的协会官网用户 ID，字符串 |
| `total` | 当前用户与原站比赛匹配的已结束 VP 总数，分页前统计 |
| `records[].id` | 唯一历史记录 ID，可用于去重；队伍的同一条记录对不同队员返回相同 ID |
| `participantType` | `individual` 或 `team` |
| `participantId` / `roomId` | 参赛身份 / 房间 ID，独立 VP 可为 `null` |
| `rank` / `rankSource` | 保存的原榜排名及其来源，不重新按本系统用户计算名次；来源不可用或旧数据时可能为 `?`、`—` 或 `legacy`，请勿直接按整数解析 |
| `solved` / `penalty` | 比赛期间通过题数 / 罚时（分钟） |
| `points` / `scoring` | 原生积分（不适用或缺失为 `null`）及计分规则 |
| `prize` / `prizeSource` | 原榜奖项及来源；可能为 `Gold`、`Silver`、`Bronze`、`None`、`Unknown` 或 `null`，未知不等于无奖 |
| `cells` | 各题结果；`solveTime` 单位为分钟；可能含原生 `points`、`maxPoints` |
| `practiceSolved` / `submissions[].practice` | 赛后补题标记；补题记录可随原 VP 返回，不应计为赛中通过 |
| `submissions` | 已同步的提交记录，无源码、密码、原站登录凭据或打印内容 |
| `status` | 最近同步的客户端状态；比赛已按服务器时间结束但客户端未再次同步时，仍可能是 `running` |
| `startedAt` / `endedAt` | 原记录的开始 / 结束时间；缺失时为 `null`，房间另有 `endsAt`（计划结束时间） |
| `createdAt` / `updatedAt` | 历史创建时间 / 最近同步时间 |
| `serverNow` | 服务器当前时间，所有时间字段均为 UTC ISO 8601 |

结束判定：房间必须已有原站时长且到达计划结束时间；不能由客户端提前发送 `complete` 绕过。独立 VP 按开始时间和已知比赛时长判定结束，手动归档可使用实际结束时间；旧客户端没有时长时，使用 `complete` / `archived` 状态和已过去的结束时间。尚不能确认结束的记录不会返回。

已找到用户但没有符合条件的记录，仍返回 `200`：`total: 0, records: []`。没有结果可能是尚未同步、比赛尚未结束、URL 不对应，或该记录属于 `live` / `bt`。

## 6. 错误响应

统一结构：

```json
{"error": "错误说明"}
```

| HTTP 状态 | 情况 |
| --- | --- |
| `400` | 缺少官网 ID / 原站 URL，参数重复、未定义参数、URL 或分页参数无效 |
| `401` | 未提供 API Key、密钥无效或已撤销，使用会话 token / Cookie 替代 API Key |
| `404` | 用户尚未绑定协会官网 ID，或接口路径不存在 |
| `405` | 外部查询接口使用了 GET 之外的方法 |
| `429` | 当前密钥一分钟内超过 120 次请求，响应含 `Retry-After: 60` |
| `500` | 服务内部错误，可稍后重试 |

响应包含 `Cache-Control: no-store`。请在服务端保存和使用密钥；调用方按 HTTP 状态码处理错误，不依赖固定的中文报错文本。

## 7. 管理接口（管理员会话）

以下接口使用正常的管理员登录 Cookie 或管理员会话 Bearer token，不能使用历史查询 API Key。Cookie 写操作仍须满足系统已有的同源校验。

| 方法与路径 | 请求 / 返回 |
| --- | --- |
| `GET /api/admin/api-keys` | 返回元数据数组：`id,name,prefix,createdAt,lastUsedAt,revokedAt` |
| `POST /api/admin/api-keys` | JSON `{"name":"协会官网训练统计"}`；名称 1–100 字符；返回上述元数据和仅本次可见的 `apiKey` |
| `DELETE /api/admin/api-keys/{id}` | 撤销并返回 `{"data":{"ok":true}}`；重复撤销幂等 |

非管理员返回 `403`；密钥不存在返回 `404`；有效密钥已达上限返回 `409`。创建及撤销均记录审计日志。应用启动时自动创建新表和索引，无需手工执行数据库迁移。
