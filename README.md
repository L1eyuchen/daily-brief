# 盖世小鸡每日资讯简报

从 13 个主机/PC 游戏资讯源抓取当日条目，按八条触发器筛选，生成一份能直接变成选题的简报，推到飞书。

核心设计：**双轨分流**——游戏资讯命中即推（每日）；手柄相关情报进待发池，攒满 3 条才发、没料就空着。

## 流程

抓取 → 去重 → 规则筛选 → 模型精筛（可降级）→ 双轨分流 → 渲染 → 推送 → 归档

## 本地运行

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt

cp .env.example .env     # 填入 DEEPSEEK_API_KEY 与 FEISHU_WEBHOOK
.venv/Scripts/python.exe main.py --dry-run   # 只跑流程不推送
.venv/Scripts/python.exe main.py             # 真推
```

未配置 `FEISHU_WEBHOOK` 时会自动降级为 dry-run。

## 配置 GitHub Actions

1. 推到 GitHub 仓库
2. Settings → Secrets and variables → Actions → New repository secret，添加两个：
   - `DEEPSEEK_API_KEY`
   - `FEISHU_WEBHOOK`
3. Actions 页签手动跑一次验证，之后每天 08:30 自动执行

## 改东西去哪儿改

| 想改什么 | 改哪个文件 |
|---|---|
| 加/删资讯源 | `config/sources.yaml`（失效源置 `enabled: false`，保留记录） |
| 调整筛选规则 | `config/rules.yaml`（黑名单、八条触发器关键词、手柄线索词） |
| 调推送条数 | `config/rules.yaml` 的 `delivery` 段 |
| 调抓取超时与并发 | `config/sources.yaml` 的 `fetch` 段 |

改配置不用动代码，这是刻意设计的——源会失效、规则会变。

## 已知限制

**图片**：RSS 能拿到封面图 URL（存在 `media:content`、enclosure 或摘要 HTML 里），但飞书自定义机器人的 webhook 无法内嵌图片——发图必须传 `image_key`，而 `image_key` 只有**企业自建应用**能上传（需要 app_id/app_secret 换 token）。所以当前只把封面 URL 写进 `archive/` 存档，不内嵌推送。要内嵌就得把机器人升级为自建应用。

**视频**：RSS 基本拿不到视频文件，给的都是 YouTube/B站 的页面链接。所以视频一律以链接形式呈现。

**源失效**：`state/health.json` 记录每个源的连续失败次数，超过阈值会推一条告警。源清单每季度应重跑一次实测。

## 降级行为

`DEEPSEEK_API_KEY` 缺失或 API 报错时，自动退回纯关键词规则，简报照发。最贵的一环不拖垮整体。
