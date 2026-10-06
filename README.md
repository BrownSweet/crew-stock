# Crew Stock

使用 **CrewAI 1.15.22 + DeepSeek + 长桥技能与只读数据** 的公司长期研究 Agent。带中文工作台，运行在独立 Docker 容器内。

## Dockerfile 部署：/crew/ + MySQL + 首次创建管理员

前端是原生 HTML/JavaScript，构建脚本将资源路径和应用路由一起编入镜像。`-t` 只指定镜像名，路径通过 `--build-arg` 指定：

```bash
cd /Users/brown/program/www/crew-stock
docker build --build-arg VITE_BASE_PATH=/crew/ -t crew-agent:2026.10.06-mysql .
```

默认构建路径为 `/`，也支持 `/crew/` 或其他绝对路径。路径属于构建配置，启动时传入 `-e VITE_BASE_PATH=...` 不会改变镜像。镜像保持非 root 单进程应用，不需要额外的容器内 Nginx。

业务数据库和 CrewAI 任务输出只使用 MySQL 8（运行时不再读写 SQLite）。先准备独立数据库，例如 `crew_stock`，以及对该库有建表、读写权限的数据库用户。程序创建表，不创建 MySQL 数据库或用户。

```bash
docker run -d \
  --name crew-agent \
  --restart unless-stopped \
  --init --stop-timeout 45 \
  -p 8765:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e 'mysql_ip=host.docker.internal' \
  -e 'mysql_port=3306' \
  -e 'mysql_root=crew_stock' \
  -e 'mysql_password=替换为MySQL密码' \
  -e 'mysql_database=crew_stock' \
  -e 'ALLOWED_HOSTS=tec.zhiquant.com' \
  -e 'COOKIE_SECURE=true' \
  -e 'FORWARDED_ALLOW_IPS=替换为外部Nginx实际来源IP' \
  -v crew_data:/data \
  -v crew_longbridge_auth:/home/agent/.longbridge \
  --read-only --tmpfs /tmp:rw,nosuid,size=256m,mode=1777 \
  --cap-drop ALL --security-opt no-new-privileges:true \
  crew-agent:2026.10.06-mysql
```

`mysql_root` 是登录用户名，不要求使用 root。数据库密码直接传入驱动，无需 URL 编码。远程 MySQL 将 `mysql_ip` 改为实际地址；同一 Docker 网络可使用容器名并添加 `--network`。容器中的 `127.0.0.1` 指向自己，不能用它连接宿主机 MySQL。该示例开放宿主机 8765 给外部代理，应限制此端口只允许可信代理访问；`FORWARDED_ALLOW_IPS` 仅填写可信代理的实际来源 IP，使 HTTPS 协议和客户端地址正确传递。

外部 Nginx 的原 `/jiami/` 配置保留，Crew 路由使用以下配置（也见 [示例](docker/host-nginx.conf.example)）。`proxy_pass` 地址末尾不要加 `/`：

```nginx
location = /crew {
    return 308 /crew/$is_args$args;
}
location ^~ /crew/ {
    proxy_pass http://43.155.214.55:8765;
}
```

沿用现有 `Host`、`X-Forwarded-For`、`X-Forwarded-Proto`、长连接超时和 `proxy_buffering off`。重新加载前运行外部 Nginx 的 `nginx -t`。

启动后打开 **https://tec.zhiquant.com/crew/**，首次在页面创建管理员账号（3–64 位字母、数字或 `_ . @ -`）和密码（至少 12 个字符）。账号创建入口只允许使用一次；无需在 `docker run` 里传登录账号密码。密码使用随机盐 scrypt 哈希，会话只存令牌哈希，保存在 MySQL，退出登录即撤销当前会话。Cookie 限定在 `/crew/`，未登录无法访问业务数据、上传或修改配置。

本机 HTTP 调试需 `-e COOKIE_SECURE=false`，通过 `http://127.0.0.1:8765/crew/` 访问；正式 HTTPS 部署使用 `true`。

```bash
docker logs --tail=100 crew-agent
docker inspect --format '{{.State.Health.Status}}' crew-agent
curl http://127.0.0.1:8765/crew/api/health
```

健康检查会查询 MySQL。前端页面 200 不代表数据库已就绪。账号状态接口为 `/crew/api/auth/status`，OpenAPI 为 `/crew/openapi.json`。

首次新库导入 `seed/` 的历史研究资料。升级必须同时保留 MySQL、`crew_data`（原始文件及模型配置）和 `crew_longbridge_auth`（长桥授权）；不要删除数据卷来更新服务。源项目 `/Users/brown/program/stock-监控` 不受影响。

### 旧 SQLite 记录完整迁入 MySQL

只在一次性迁移脚本中读取 SQLite，运行中的应用不支持 SQLite 回退。先停止旧应用，备份旧数据卷和授权卷；准备**空的专用 MySQL 数据库**与新的 `crew_data` 卷。旧目录不要原地覆盖，新应用发现旧 SQLite 文件会拒绝启动，避免误把历史数据当成新库。

旧 Compose 部署的卷通常是 `crew-stock_research-data` 和 `crew-stock_longbridge-auth`，请用 `docker inspect` 确认。以下迁移只读挂载旧数据，新目录保留 blob、记录快照、设置等文件；数据库内逐表迁移全部对象、ID 序列、证券归属、元数据和档案关联，并校验内容哈希。旧 CrewAI 最新任务缓存也导入 MySQL，归档在 `legacy-cache` 下；旧备份留在备份目录，不作为运行数据库。首次账号随后在页面创建。

```bash
docker stop crew-stock-app-1
# 先完成旧数据卷与授权卷的独立备份，再运行一次性导入：
docker run --rm \
  --add-host=host.docker.internal:host-gateway \
  -e 'mysql_ip=host.docker.internal' \
  -e 'mysql_port=3306' \
  -e 'mysql_root=crew_stock' \
  -e 'mysql_password=替换为MySQL密码' \
  -e 'mysql_database=crew_stock' \
  -v crew-stock_research-data:/legacy:ro \
  -v crew_data:/data \
  crew-agent:2026.10.06-mysql \
  python /app/scripts/migrate_sqlite_to_mysql.py --source-dir /legacy --target-dir /data
```

确认输出 `verified: true` 后，用上面的 `docker run` 启动新应用，但授权挂载改为 `-v crew-stock_longbridge-auth:/home/agent/.longbridge`，沿用原授权。旧研究数据不会被测试资料替换，旧备份不删。若需回滚，在产生新业务写入前停止新容器并重启旧容器；新旧数据库不会自动双向同步。

## 配置连接

在工作台左下角「连接设置」完成：

1. 输入自己的 DeepSeek API Key，保存后点击「测试模型连接」。默认 `deepseek-flash`，可改为账户支持的 `deepseek-*` 模型。使用官方 `https://api.deepseek.com`。
2. 打开 [长桥官方授权页](https://open.longbridge.com/connect)，取得一次性授权码，在工作台完成授权并测试行情连接。授权属于本容器，不沿用 Codex 插件或本机 CLI 的登录。
3. 回到研究工作台，选择英伟达或新建公司档案，输入研究问题。

也可以使用容器设备授权：

```bash
docker exec -it crew-agent longbridge auth login --client-name 'Crew Stock Docker'
```

如果使用环境变量配置 DeepSeek，将 `.env.example` 复制为 `.env` 后填写。页面保存的配置优先于环境变量；页面不会返回已有 Key。`.env` 不进入镜像和 Git。

## 三种运行方式

| 方式 | 数据来源 | 是否调用 DeepSeek |
| --- | --- | --- |
| 最新数据 + AI 研究 | 本次长桥采集，必要时 Agent 追加只读查询 | 是 |
| 仅采集最新证据 | 本次长桥采集 | 否 |
| 基于历史资料研究 | 明确导入的历史证据，不联网补行情 | 是 |

历史模式不会把历史采集日期更新为今天。任何采集失败均保留失败证据，不会静默切换供应商或使用旧数据替代。

## 已实现

- 一家公司对应多个证券，证券归属唯一；确定性 ID 分配和 MySQL 事务。
- 长桥公司资料、行情、季度利润表（完整研究另采资产负债与现金流）、业务分部、一致预期、估值、新闻、公告的基线采集；Agent 可追加日 K 线及新闻/公告正文。
- 真正的 CrewAI Flow：采集 → 一个研究 Agent 的研究/结构化两个 Task → Pydantic 与引用校验 → 报告落库。
- 运行时加载长桥基础、基本面、资讯及财报分析四个 Skill；参考文件通过受限工具读取，报告记录 Skill 哈希。
- 原始响应 SHA-256、不可变内容去重、采集状态、命令参数、上次证据引用、结构变化及可精确计算的标量差值。
- 中文报告、事实/推测/情绪/观点/批注分类、证据回溯、缺口、反方证据、待确认投资逻辑。
- 人工确认或驳回及审阅历史；只有明确确认才更新当前投资逻辑。
- 原文与用户批注分别保存，作为后续研究的未核实资料。
- 持久研究队列、重启中断标记、默认暂停的间隔跟踪计划。容器停机期间不执行；重新启动后过期计划执行一次，不补跑全部错过周期。

新建数据用 MySQL 作为事务索引，原始内容位于内容寻址的 `blobs/`，证据/报告另有 `records/<id>/<id>.json` 快照。导入的旧 YAML/CSV 档案保留，不继续作为新记录的可写主库。

## v0.2：八项长期研究能力

打开左侧 **公司研究中心**，先选择公司。完整操作见 [使用指南](docs/RESEARCH_GUIDE.md)。

| 能力 | 页面 / 行为 |
| --- | --- |
| 长期研究档案 | 时间线、全文搜索、证据链接、JSON 导出；研究 Agent 可检索历史记录 |
| 财报与电话会整理 | 长桥三表、财报概览、公告与电话会新闻发现，提取摘要、管理层问答和缺口；支持原文导入 |
| 管理层承诺对照 | 提取/登记承诺、条件和截止日，核对后与实际指标作数值检查，保留每次评价 |
| 行业与竞争对手 | 2–6 家公司的比较组、成员修改历史、整组研究、长桥行业排名证据、同口径指标表 |
| 预期与指引变化 | 指标版本、指引修订、实际与事前预期比较；供应商 Beat/Miss 单列 |
| 事实、推测和情绪 | 报告逐条分类、引用、置信度；用户原文保留未核实标识 |
| 投资逻辑变化 | 确认报告时原子保存变化前/后、确认原因、触发证据、对应报告 |
| 决策过程复盘 | 决策行动、原始假设、失效条件、替代方案、风险、当时证据/逻辑快照；逐项复盘与 AI 辅助核查 |

自动提取记录先标为待确认。程序校验引用和原文摘录，并拦截模型补出的日期、币种、口径和原文未出现的数值；这仍不等于自然语言语义已被证明。没有电话会原文时会留下缺口，不生成“完整逐字稿”。

人工指标对比使用 Decimal，并要求同公司、期间、单位、币种、会计口径和评价方向；预期差还要求预期记录在结果公布前已保存。长桥自身提供的实际/预期对照不具备完整预测形成时间，因此单独标注，不冒充本地事前预测。v0.2 的决策入口保留研究日志；v0.4 新增开仓方案和本地模拟委托，仍不会向券商下单。

## Docker 隔离

- UID/GID `10001:10001`，只读根文件系统、移除 capabilities、禁止新增权限；临时缓存使用 `/tmp` tmpfs。
- 原始证据与配置使用独立 `/data` 卷，OAuth 使用 `/home/agent/.longbridge` 卷，业务记录与账号在 MySQL。
- 不挂载宿主机源码、Docker socket 或宿主机账户凭据。容器需要访问 MySQL、长桥与 DeepSeek。
- 只提供只读市场数据工具，不提供券商实盘交易；模拟交易只修改 MySQL 内的虚拟账本。
- 单一 MySQL 数据库只允许一个后台工作器，通过数据库租约防止两个容器重复执行任务。

## 开发与测试

```bash
uv sync --frozen
# 提前创建专用 crew_workspace_test 库；以下变量只用于当前测试 shell。
export mysql_ip=127.0.0.1 mysql_port=3306 mysql_root=crew_test
export mysql_password='替换为测试数据库密码' mysql_database=crew_workspace_test
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

可在生产镜像中验证 CrewAI 的 Linux 运行路径（脚本使用临时数据和测试模型，不伪装成真实 DeepSeek 结果）：

```bash
# 必须在独立 *_test MySQL 数据库和临时数据目录中运行，不能在正式库执行
uv run python scripts/container_smoke.py
```

测试数据库必须以 `_test` 结尾，测试会清空该库的业务表。`v04/v05` 验证脚本同样拒绝非测试数据库；临时文件目录不能替代数据库隔离。

测试覆盖 ID 并发、归属唯一性、证据哈希与篡改检测、失败采集、命令白名单、路径限制、引用校验、真实 CrewAI 工具循环和审阅落库、DeepSeek HTTP 请求契约、API 状态与来源校验。自动化测试中模型与行情使用替身；旧版本曾完成真实长桥采集和 DeepSeek 报告生成，同步/异步结构化调用均通过。本次 MySQL 迁移未重新调用真实外部服务，最新验收见 [验证记录](docs/VERIFICATION.md)。

## 实施边界

v0.3 在八项长期研究能力上新增独立行业档案与行业级定时研究、文件与图片解析、可交互供应链影响图和有版本快照的冲击情景。公众人物/舆情传播独立图通道、音频转写、外部消息推送仍未实现。详见 [使用指南](docs/RESEARCH_GUIDE.md) 和 [项目检查与实施记录](docs/IMPLEMENTATION.md)。

默认采用非思考模式，并要求模型输出正文；不会用 `reasoning_content` 代替研究结论。通过 CrewAI ReAct 协议调用只读工具，JSON 由本地 Pydantic 校验，避免依赖 OpenAI 专用的严格 JSON Schema 接口。不存在自动切换模型或数据源。

## 官方文档与技能来源

- [用户指定的 CrewAI 文档索引](https://docs.crewai.com/llms.txt)
- [本项目对应版本：Flow](https://docs.crewai.com/v1.15.22/en/concepts/flows)
- [CrewAI Skills](https://docs.crewai.com/v1.15.22/en/concepts/skills)
- [CrewAI LLM 配置](https://docs.crewai.com/v1.15.22/en/concepts/llms)
- [DeepSeek 官方 API](https://api-docs.deepseek.com/)
- [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)
- [长桥开发者文档](https://open.longbridge.com/llms.txt)

`skills/` 来自已安装的 Longbridge 插件 2.0.0；为兼容 CrewAI，将两个技能的 YAML 布尔元数据改为字符串，其正文与参考文件保持上游内容，上游许可保留在 `licenses/longbridge-skills-LICENSE`；文件校验值记录于 `docs/skill-manifest.json`。长桥 Linux CLI 固定 `v0.28.7` 并校验下载包 SHA-256。



## v0.3 新入口

| 页面 | 操作 |
| --- | --- |
| 行业研究中心 | 新建行业 → 引用已有公司并填写角色 → 整行业研究 / 配置周期观察 |
| 文件与图片 | 选择公司或行业 → 上传 PDF、图片、DOCX、XLSX 或文本 → 查看页级解析证据 |
| 供应链影响图 | 登记 / AI 提取关系 → 核对来源与权重 → 选择冲击源并计算情景 |

图片/PDF 使用 DeepSeek 官方 `deepseek-flash` 多模态接口（[官方图像理解文档](https://api-docs.deepseek.com/zh-cn/guides/vision/)）。每文件 12 MiB，PDF 每次选定最多 20 页；Office 仅读取文字表格。图谱默认不编造关系，情景结果只用于检验假设。具体边界见完整指南。


## v0.4：先识别归档，再用模拟交易检验决策

- **文件与图片**默认可独立上传，AI 识别资料类型、公司、行业和标签。一个原件可关联多个档案；明确引用正文、已有唯一标识且置信度达到 0.9 时自动关联，否则保留候选，人工核对。
- **决策与模拟**选择公司和证券，生成经营→盈利→估值→股价区间的研究方案。读取公司档案、行业研究与图谱假设，并使用长桥新采集的证券信息、报价、最新成交、财务、一致预期和估值。EPS × PE 由程序计算；数据不适用/不足时保持观察。
- 创建独立币种的虚拟资金账户，测算限价、风险比例、资金上限和交易单位，人工确认后提交模拟委托。支持部分成交、撤单、模拟平仓、止损/止盈触发、资金冻结、费用/滑点和盈亏记录。
- 账户可手动获取行情，也可显式启用每分钟跟踪；后台队列繁忙或容器停止会延迟。委托≠成交，模拟结果≠券商可执行价格。
- 原方案、引用哈希和预算随模拟决定冻结；复盘独立追加，记录逻辑变化、过程质量与下一步。

完整步骤、撮合假设和限制见 [研究使用指南](docs/RESEARCH_GUIDE.md)。默认未创建模拟账户、未启用模拟跟踪，也没有接入实盘订单接口。

## v0.5：供应链订单与履约观察

进入 **供应链订单**，可选择公司/行业后点击 **AI 自动采集订单线索**，无需先上传文件或登记订单。系统按所选范围读取长桥新闻、公告及匹配关键词的正文，由 DeepSeek 提取带来源的待核对候选；采集范围和缺口显示在分析记录。也可从资料提取候选或手工登记，核对交易方、引用、金额和日期后确认入图。货物和合同货款分别显示流向；支持未上市交易方、多笔订单、分期交付/验收/付款、模糊期限与确认版本回放。

每笔订单可显式启用持续观察，读取新增档案及长桥新闻、公告。AI 仅提交新订单或变更建议，人工审核后才进入订单图或修改订单。页面提供 90/30/7 天提示和“到期待核查”；未发现披露不等于违约。已确认订单进入公司研究和决策上下文，合同金额不直接视为收入或现金流。

完整步骤和数据边界见 [供应链订单使用指南](docs/ORDER_GUIDE.md)。新订单的持续监控默认暂停，提醒显示在项目页面；未创建示例企业之间的真实订单。
