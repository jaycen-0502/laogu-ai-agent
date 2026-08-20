# 账号自动化状态 API

登录 Cookies、站点本地存储仍由每个浏览器实例自己的 `user-data-dir` 保存。以下数据保存在老谷浏览器 SQLite 数据库中：

- 账号关键词与脚本游标：`profileId + platform + scriptId`
- 已处理对象去重：`profileId + platform + itemType + itemKey`
- 每日计数：`profileId + platform + date + counterKey`

服务默认监听 `http://127.0.0.1:19876`，只接受本机请求。如果配置中启用了 API Key，所有 `/api/*` 请求都需要携带配置的认证头，默认头名为 `X-Ant-Api-Key`。

## Python 示例

```python
import requests

BASE_URL = "http://127.0.0.1:19876"
HEADERS = {
    "Content-Type": "application/json",
    # 启用 API Key 后取消下一行注释：
    # "X-Ant-Api-Key": "你的 API Key",
}

scope = {
    "profileId": "浏览器配置 ID",
    "platform": "x",
    "scriptId": "my-review-script",
}

# 保存关键词
response = requests.put(
    f"{BASE_URL}/api/automation/account-state",
    headers=HEADERS,
    json={**scope, "keywords": ["AI", "Playwright"]},
)
response.raise_for_status()
print(response.json())

# 读取关键词与游标
response = requests.get(
    f"{BASE_URL}/api/automation/account-state",
    headers=HEADERS,
    params=scope,
)
response.raise_for_status()
print(response.json()["state"])

# 检查并记录一个已人工审核对象
item = {
    "profileId": scope["profileId"],
    "platform": "x",
    "itemType": "user",
    "itemKey": "用户或帖子的稳定 ID",
}
status = requests.post(
    f"{BASE_URL}/api/automation/processed/check",
    headers=HEADERS,
    json=item,
).json()["status"]

if not status["processed"]:
    requests.post(
        f"{BASE_URL}/api/automation/processed/mark",
        headers=HEADERS,
        json={**item, "metadata": {"decision": "reviewed"}},
    ).raise_for_status()

# 当日计数加一
requests.post(
    f"{BASE_URL}/api/automation/counters/increment",
    headers=HEADERS,
    json={
        "profileId": scope["profileId"],
        "platform": "x",
        "counterKey": "reviewed",
        "delta": 1,
    },
).raise_for_status()
```

## 内置 Playwright 脚本

脚本的 `run(api)` 可使用：

```javascript
const saved = await api.state.get({ profileId, platform: "x" });
await api.state.saveKeywords({ profileId, platform: "x" }, ["AI"]);
await api.state.saveCursor({ profileId, platform: "x" }, { nextPage: 2 });
const status = await api.state.isProcessed({
  profileId,
  platform: "x",
  itemType: "user",
  itemKey: userId,
});
await api.state.markProcessed({
  profileId,
  platform: "x",
  itemType: "user",
  itemKey: userId,
  metadata: { decision: "reviewed" },
});
await api.state.incrementCounter({
  profileId,
  platform: "x",
  counterKey: "reviewed",
});
```

`scriptId` 默认由运行器自动使用当前脚本 ID；不同浏览器配置、平台和脚本之间不会共用关键词或游标。
