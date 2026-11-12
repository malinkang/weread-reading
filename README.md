# 读书记 / WeRead Public Reading Site

把微信读书同步到 Notion 的数据，发布成一个公开静态书架。

公开站：<https://reading.malinkang.com/>

## 架构

```text
Notion 数据库
    │  GitHub Actions（每天 03:00 Asia/Shanghai）
    ▼
data/public-snapshot.json   ← 清洗后的公开快照，提交回仓库
    │  Cloudflare Pages（Git 集成，自动构建）
    ▼
静态 HTML + 搜索索引        ← 所有书籍页预渲染
    ▼
https://reading.malinkang.com
```

页面渲染不依赖 Notion API。Notion token 只存在于 GitHub Actions Secret，不会进入浏览器或 Cloudflare 构建环境。

## 为什么缓存 JSON

`actions/cache` 是临时缓存，可能被淘汰，不能作为公开站点的稳定数据源。这个项目把清洗后的公开快照提交到仓库：

- 每次更新可追溯、可 review；
- Cloudflare Pages 构建时直接读取本地 JSON；
- 数据变化才产生新提交，不会每天制造空 commit；
- 构建失败时仍能回到上一个可用的公开快照。

`actions/cache` 只用于 Python 依赖和封面下载缓存，不承担数据源角色。

## 本地预览

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# 使用演示数据
python make_demo.py
python build_site.py --snapshot data/demo-snapshot.json --output site --no-download-covers

# 或使用仓库中的真实公开快照
python build_site.py --snapshot data/public-snapshot.json --output site

python -m http.server 8000 --directory site
```

打开 <http://localhost:8000>。

不要直接双击 `site/index.html`，因为站点使用 `/assets/...` 根路径，必须通过 HTTP 服务访问。

## 本地拉取真实数据

```bash
export NOTION_TOKEN=...
export WEREAD_BOOKS_DATABASE_ID=...   # 也接受 WEREAD_BOOKS_DATA_SOURCE_ID
export WEREAD_AUTHORS_DATABASE_ID=...
export WEREAD_CATEGORIES_DATABASE_ID=...
export WEREAD_BOOKMARKS_DATABASE_ID=...
export WEREAD_CHAPTERS_DATABASE_ID=...
export WEREAD_DAILY_DATABASE_ID=...
export WEREAD_READING_LOG_DATABASE_ID=...

python sync_notion.py --output data/public-snapshot.json --print-summary
python build_site.py \
  --snapshot data/public-snapshot.json \
  --output site \
  --site-url https://reading.malinkang.com
```

## GitHub Actions

工作流：`.github/workflows/site.yml`

触发方式：

- 每天 `19:00 UTC`（北京时间 03:00）自动运行；
- 在 Actions 页面手动运行 `workflow_dispatch`。

流程：

1. 从 Notion 拉取公开字段；
2. 写入 `data/public-snapshot.json`；
3. 快照有变化时自动提交并推送到 `main`；
4. Cloudflare Pages 监听 `main` 的提交并自动重新构建。

需要配置的 GitHub Secrets：

| Secret | 必填 | 说明 |
|---|---|---|
| `NOTION_TOKEN` | 是 | Notion integration token |
| `WEREAD_BOOKS_DATABASE_ID` | 是 | 书籍数据库 |
| `WEREAD_AUTHORS_DATABASE_ID` | 否 | 作者关系库 |
| `WEREAD_CATEGORIES_DATABASE_ID` | 否 | 分类关系库 |
| `WEREAD_BOOKMARKS_DATABASE_ID` | 否 | 划线数据库 |
| `WEREAD_CHAPTERS_DATABASE_ID` | 否 | 章节数据库 |
| `WEREAD_DAILY_DATABASE_ID` | 否 | 每日阅读时长数据库 |
| `WEREAD_READING_LOG_DATABASE_ID` | 否 | 阅读日志备用数据源 |

可选 Repository Variables：

| Variable | 默认值 | 说明 |
|---|---|---|
| `PUBLIC_SITE_TITLE` | `读书记` | 站点名 |
| `PUBLIC_SITE_URL` | `https://reading.malinkang.com` | 自定义域名 |
| `PUBLIC_SITE_DESCRIPTION` | 内置文案 | 首页描述 |
| `PUBLIC_INCLUDE_HIGHLIGHTS` | `true` | 是否公开划线 |
| `PUBLIC_INCLUDE_REVIEWS` | `false` | 是否公开想法/书评 |
| `PUBLIC_MAX_HIGHLIGHTS_PER_BOOK` | `40` | 每本书最多公开多少条 |
| `PUBLIC_MAX_HIGHLIGHT_CHARS` | `500` | 单条划线最大字符数 |

**公开版默认不拉取 Reviews。** 私人想法不会因为配置错误进入公开快照。要公开书评，必须显式设置 `PUBLIC_INCLUDE_REVIEWS=true`。

## Cloudflare Pages

项目：`weread-reading`

- 生产分支：`main`
- 构建命令：`pip install -r requirements.txt && python build_site.py --snapshot data/public-snapshot.json --output site --site-url https://reading.malinkang.com`
- 输出目录：`site`
- Python：`3.12`
- 自定义域名：`reading.malinkang.com`
- 域名入口：Cloudflare Worker `weread-reading-edge` 代理到 `weread-reading.pages.dev`

首次连接 Git 后，Cloudflare Pages 会在 `main` 有新提交时自动构建。GitHub Actions 只提交公开 JSON 快照，不直接部署页面。

### 域名代理

Cloudflare Pages 目前会拦截 `malinkang.com` 下新增的子域名，因此 `reading.malinkang.com` 由仓库内的 `edge/worker.js` 代理到 Pages：

```bash
wrangler deploy --config edge/wrangler.jsonc --domain reading.malinkang.com
```

如果以后 Cloudflare 解除限制，可以改为在 Pages 项目中直接绑定自定义域名，并删除这个代理 Worker。

## 数据与隐私

- 公开快照只包含书名、作者、分类、封面、阅读状态、阅读时长、评分、公开划线和聚合统计。
- `Reviews` 默认不查询；即使 Notion 中有私人想法，也不会出现在公开快照。
- 划线是短摘录，不是全文；默认每本最多 40 条、每条最多 500 字。
- 书籍封面会下载到 `assets/covers/`；下载失败时生成本地 SVG 封面，避免外链失效。
- 不把 Notion token、微信读书 cookie 或完整私人书评写进仓库。

## 验证

```bash
python -m pytest -q
python build_site.py --snapshot data/demo-snapshot.json --output site --no-download-covers
```
