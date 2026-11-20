# Week 2 Day 5 实现计划：让理赔员审核 Agent 的改进

已获学生批准实施。**一次改进的交付路径**是 Dana 登录，查看当前版本的错误和提案差异，批准候选配置，再由后端用留出集裁决是否启用。随后 Dana 可以审批下一轮使用的 recipe。审批一次配置只授权试验；通过裁决才会改变下一次正式派单。

课程说明：已阅读 `labs/week2day5.ipynb`。Judge、配置版本和 improver 沿用实验的设计、评分口径、首版 recipe 和四个工具名。产品行为以 `docs/IMPROVE.md` 为准，包括持久审批、逐轮预算以及由后端保管参考答案。实施时每阶段通过下列检查，勾选本文件，更新 `docs/STATUS.md`，通过仓库 hook 提交该阶段。

## 1. 先让理赔员登录并以本人身份操作

在 Dashboard 入口恢复 Supabase session，再加载业务页面。浏览器通过独立的 `@supabase/supabase-js` 客户端调用 `signInWithPassword`，业务请求通过现有 `dashboard/src/api.ts` 携带 access token。FastAPI 用 PyJWT 的 `PyJWKClient` 校验项目公钥、签发方、有效期和 `authenticated` audience，从已验证的 email 查后端 handler 记录和权限。当前种子已有 email，三人的权限均为 true，需把 Marcus 和 Priya 改为 false。Dana 保留高级权限。

- [x] 登录入口只提供邮箱和密码，所有业务页直达、刷新及退出均遵守登录状态。页头展示当前理赔员。开发启动使用根目录的 `VITE_SUPABASE_URL` 和 `VITE_SUPABASE_PUBLISHABLE_KEY`，Vite 测试模式只用测试配置。后端在启动时读取项目地址，使用其 `/auth/v1/.well-known/jwks.json`，运行环境包含 `pyjwt[crypto]`。
- [x] 对本分支的 `POST /api/dispatch`、`POST /api/evaluations`、人工发起单案和 `POST /api/claims/{id}/sign-off` 做 API 测试。缺失、过期或无效的 token 返回 401，有效身份无法匹配 handler 或权限不足返回 403，拒绝时无写入。单案 CLI 继续能以已认证的理赔员发起工作。agent service 的内部执行、记录和恢复沿用服务间调用。
- [x] 在 `DecisionPanel.tsx` 移除签批人选择。Marcus、Priya 能发起派单和评估，查看待签赔付时能读到权限原因；API 拒绝两人签批。Dana 签批后只记录其本人和时间，原线程恢复并支付一次。测试覆盖请求体冒用他人身份。
- [x] Playwright 使用本地 Auth 替身签发非对称测试 token，提供 JWKS、密码登录和浏览器所需 CORS；既有场景也走登录流程。Vitest 验证失败重试、会话恢复和退出。官方接口参考见 [密码登录](https://supabase.com/docs/reference/javascript/auth-signinwithpassword) 和 [JWT 校验](https://supabase.com/docs/guides/auth/jwts)。

## 2. 固定每次运行使用的版本，上线 Configuration 和 Settings

后端沿用 SQLite／Postgres 共用的存储路径。seed 从 `backend/instructions.md` 建立配置 v1（Luna、low），从放在该文件旁的实验首版 recipe 建立 recipe v1。版本内容不可变，单独记录当前版本。每次 dispatch 或 Scoreboard 手动评估在获准启动时绑定当前配置；轮内评估显式绑定待测配置。服务用所绑定的指令、模型和 effort 构造 claim agent，保留图、工具、签批与支付流程。

设置从 `data/company.json` 取初始审批限额 $1,000，从 `data/` 新文件读取其余初值。业务设置写入后端数据库，改动带操作者和时间。每个回合在开始时固定改进设置、允许模型和 recipe 版本。默认每轮 $0.50，评估并发数 25。下表中的成本与时间均按旧值的比例比较。

| 留出集指标 | 业务下限 | 超过此幅度才算改善（noise） | 允许的最大退步（tolerance） |
| --- | ---: | ---: | ---: |
| 准确率 `accuracy` | 60% | 10 个百分点 | 10 个百分点 |
| 解释平均分（0–3） | 1.00 | 0.15 分 | 0.15 分 |
| 理赔模型费用（美元/案） | — | 降低 15% | 增加 100% |
| 理赔活跃耗时（秒/案） | — | 缩短 15% | 增加 25% |

| 课程模型白名单（Settings 只读） | 允许的 effort | 输入美元/百万 token | 输出美元/百万 token |
| --- | --- | ---: | ---: |
| `openai/gpt-6-luna` | none、low、medium | 0.10 | 0.50 |
| `google/gemini-3.5-flash-lite` | minimal、low | 0.30 | 2.50 |
| `deepseek/deepseek-v4.1-flash` | low | 0.30 | 1.20 |

- [x] seed 后 Configuration 取代 Instructions，能看当前指令、版本历史、各版本留出分数、变更理由、提议者、批准者和时间。用两份版本夹具验证后端 `difflib` 产生逐行差异，能看到与上一版相比的模型和 effort 变化。种子版本明确标识来源，尚无评估的数据标为待评估。
- [x] 用 stub 捕获派单、手动评估和候选评估中实际传给模型的配置，核对 run 记录的版本；重试或恢复不用重新解析当时的当前指针。claim agent 获取版本的接口只提供运行配置，不含分数和参考答案。已有业务记录可重开，缺少版本出处的历史运行如实标识。
- [x] Settings 展示表中所有设置、预算、并发及审批限额。Dana 确认后可修改可写项，记录值和审计信息；Marcus/Priya 可查看原因，修改请求返回 403。所有人可切换 light、dark、system，刷新后保留本浏览器选择，业务设置不受影响。
- [x] 在 `Finance.decide` 首次写入时保存当时的限额；队列、详情、签批、付款和重试均读该决策快照。计算新赔案时使用生效设置。夹具中两笔均为 $1,250，先后把限额从 $1,000 调到 $1,500：第一笔仍需签批，第二笔可直接付。已有决策按调整前限额补齐，重放与同额边界有测试。
- [ ] 有 `MODEL_NAME` 时，claim agent 与 improver 只用该模型，effort 记录为 `default`，有效价格为零；不向模型发送不支持的 effort。`none` 仍可表示 Luna 的有效等级。此路径用替身测试全部流程，免费账户不做付费回合验收。

## 3. 给每案解释评分，并把费用计入正确的运行

扩展 `backend/eval_sources.py` 的 `handler_references`，导入两套标签中的 reason 和 reference。在后端单独封装 Jev（`typesafe/jev-1.13`），复用 notebook 的三问。输入为双方的决定、handler 的理由与条款，以及 agent 的解释与所引条款。将评分接入 `Swarm.close` 的评估收尾，先批量读数据，释放数据库连接后并行调用，再持久化每案结果。Scoreboard 的读取只展示已存结果。

- [ ] 测试替身收到一次 OpenRouter Decisions 调用和三问。环境中的 judge origin 只读一次，每次调用显式传 `server_url`，超时 10 秒，关闭 SDK 重试。25 案含超时的并行收尾在 20 秒内完成，重读页面和重复收尾不会重复评分或记账。
- [ ] 按 Jev 返回的 `score` 记录 0–3 分，按 F/C/B/A 中概率最大的等级显示字母。以故意让字母和分数四舍五入结果不同的夹具验收。保留 same_grounds、same_section 的概率，在页面显示是／否。真实决定缺失直接给 F/0 并跳过调用；judge 失败标为未评分，平均值排除该案；全部未评分时均值为空。免费模式所有案件显示未评分且零次外呼。
- [ ] Scoreboard 增加平均解释分，详情与逐案决定并列显示等级和两项检查。原有准确率、错判成本、单案费用与耗时仍按既有口径计算。分别核对理赔模型费用和从 Jev 响应读取的费用，两笔均可归属该评估。

## 4. 用确定性后端规则管理回合、预算和版本裁决

后端保存回合、设置快照、关联版本与运行、待审提案、审批历史、费用和裁决。同一时间只有一个未结束回合。先为当前版本按缺口补齐 **1 次全量 evaluation + 2 次全量 held-out**；候选获批后同样跑 **1+2**。每次创建独立赔案副本。复用 `backend/scoring.py` 的 `accuracy`：决定一致且赔付差不超过 $1；每案时间取实际活跃工作时间。

- [ ] 手算验证每个版本按数据集对回合发起的完整全量评估取运行均值。混入 tiny、手工 Scoreboard 和中止运行不会改变版本分数或填满基线名额。已有 1 次 evaluation、1 次 held-out 时只补 1 次 held-out。验证评估副本不会改变正式队列。
- [ ] 候选的两次留出运行均值达到两项下限，至少一项改善严格超出 noise，且没有一项退步超过 tolerance，才在一个事务中记下裁决并切换当前版本。以旧值（0.72、1.50、$0.002、10 秒），新均值（0.84、1.50、$0.004、12.5 秒）验收保留；把耗时换成 12.6 秒时回滚。另测仅等于 noise、未达下限、无有效 grade、不能完成候选评估的回滚理由。成本与耗时用旧值乘比例比较，避免旧值为零时做除法。
- [ ] 预算按回合累计 improver 的 OpenRouter `usage.cost`、全部基线及候选评估的理赔费用与 judge 费用。按目标模型历史最高单案费用 × 案数估算；新模型用当前模型数据乘输入、输出单价比中的较大值。测试 Luna 换 Gemini 的系数为 5，预估超过余额时拒绝启动。首次 seed 无历史时，建议在首个必要的基线评估内先放行一案取得费用样本，再检查剩余案件；它计入同一 run，不额外启动试跑。
- [ ] 并发调度在每起实际开案前检查停止标记和实付余额；模型调用、重试及评估启动均受回合状态约束。已在途的费用据实归账，达到限额后不再开新案。用可控制放行时机的 stub 验证运行显示 Stopped，候选不会启用；不足以建立基线时结束并给出原因。OpenRouter key 的信用额度覆盖三类调用。
- [ ] 后端为 improver 单独生成 `read_results` 视图。含当前配置、两套分数、最新全量 evaluation 的全部错案和评分最低的五个正确案、双方决定／赔付／理由与引用、等级和两项检查，另有历史回合、目标、noise、tolerance、裁决规则、剩余预算与允许模型。用可识别的合成标签验证所有返回路径（包括历史与裁决）只给出留出集汇总分；claim 工具读不到任一集合的答案或分数。

## 5. 接通 improver、人工审批和 Improvement 页面

`agent/improver.py` 用 LangChain `create_agent`，默认 Luna、medium，关闭并行工具调用，逐轮限制模型调用次数（跨暂停／恢复累计）。用目标式 instructions 说明理赔员可用的保单条款、手册、历史与工具，要求提案使用可推广的规则，禁止写入具体赔案名。recipe 作为独立数据注入；第一版按实验逐项读结果、补规则、保持模型和 effort 提案、评估、改进 recipe 并保留最后一步。

- [ ] 只注册 `read_results`、`propose_change`、`run_evaluation`、`propose_recipe`，通过独立的 improver HTTP client 调后端。在现有 `agent/main.py` 的 claim 工具集中检查隔离。对修改目标、预算、容忍度、模型目录、审批限额、数据集、judge 没有写入口。模型或 effort 超出白名单在人工中断前退给 improver，页面没有待审项。
- [ ] 两个 propose 工具使用 `HumanInTheLoopMiddleware`，每轮新 UUID thread，独立 `PostgresSaver` 共享已有连接池，`durability="sync"`，本地沿用文件 checkpoint。后端接收 handler 的审批/拒绝并转发；服务取得工作权后先经后端记录处理人、时间和原因，再恢复模型。拒绝理由进入同一线程，允许重新提案。批准配置只建立候选，批准 recipe 后才生成供后续轮次用的新 recipe。
- [ ] 扩展 `agent/service.py` 的工作锁。dispatch、外部评估、运行中的 improver 三者互斥，冲突响应 409 明确写出正在执行的工作；等待人工时释放锁但保留回合。回合自己的评估直接复用 dispatcher，在新的 `contextvars.Context` 执行。测试两个并发开轮只有一个成功，等待审批时能派单，派单期间审批或拒绝都无记录，重复提交不会创建第二个候选或第二次评估。
- [ ] 服务重启后，等待审批的回合恢复同一份提案；执行中被重启打断的回合标 Stopped。人工停止时后端先将状态落库，拒收后续业务状态变更，服务不再启动赔案或模型调用，在途工作收尾后释放锁。测试停止与裁决竞争：以已提交裁决为界，此前保持旧版本，此后保留裁决结果。回合中途改 Settings 只影响后续回合；模型未提案就结束时显示无试验版本。
- [ ] 页面轮询后端记录，依次可见基线进度（第几次／3）、improver 工作中、配置待审、候选评估进度、裁决、recipe 待审和结束／停止。Dana 可开始、停止、批准、带理由拒绝；其余 handler 可查看权限原因。用同一个 diff 组件展示两类提案和版本历史，包括指令增删色及模型、effort 的前后值。
- [ ] 对开始／停止回合、批准／拒绝两类提案、更改设置做参数化 API 测试：匿名请求返回 401，Marcus 与 Priya 返回 403，回合状态和审计记录均保持原值。检查新增操作的 OpenAPI：每个操作有唯一 `operation_id`、单行 docstring，且所有输入必填。
- [ ] 历史按新到旧列出 recipe、设置、候选、原因、经办人与时间、留出集前后分数、裁决理由和费用明细。原生 SVG 按试过的版本绘制留出准确率与解释分，注明各自单位，缺分处留空。测试每轮只有一个以回合命名的 Langfuse session；各评估赔案保留原 run 标签并新增 round 标签。

## 6. 统一页面体验，做离线回归和限定的实跑

- [ ] 依 `docs/BRAND.md` 检查登录、队列、赔案、Scoreboard、评估详情、Configuration、Improvement 和 Settings 等全部页面。在笔记本尺寸及两种主题核对标题用途、当前导航、间距、字体、表格数字右对齐及单位、状态文字和颜色、键盘操作、加载／空／错误的下一步提示。轮询保留焦点、滚动位置和审批输入；更改 agent 或设置前用应用自身的确认对话框，取消时无写入。
- [ ] 在隔离副本移除项目 `.env` 后运行 `npm run check`，通过 Ruff、Pyright、pytest（覆盖率 ≥80%）、ESLint、Prettier、tsc、Vitest（语句覆盖率 ≥80%）和 Playwright。模型、judge、Auth、agent 服务均使用对应测试替身或本地夹具。E2E 真正登录，借测试 API 推进提案，再从页面审批、改业务设置、切主题并刷新。保留现有赔案浏览、派单、评估和支付回归；启动脚本兼容 macOS 和 Windows。
- [ ] 实跑前核对 lab 设置（3 名已建的 Auth 用户、关闭注册、公开签名密钥、`HANDLER_PASSWORD` 和 key 信用额度）。确认课程目标库并停服务，仅在这组实跑前执行一次 `npm run seed`；它会重建目标库的 UnderCover 业务数据，将配置与 recipe 归 v1、清空回合。随后 `npm run dev`，用默认模型和 Playwright MCP 登录 Dana，连续跑两个回合。首个提案带理由拒绝，批准下一次及之后各提案。记录过程、版本 diff、每轮留出集前后指标、裁决与费用。用 Langfuse API 按回合核对 session 和标签。
- [ ] 对 v1 两次留出运行列出四项指标的原始值、实际差值、noise 与 tolerance。准确率用百分点、grade 用分、费用与时间用相对变化。随后仅发起一次 N=25 的当前版本 dispatch：Marcus 在页面与后端均无法签批待付赔案或更改设置；Dana 对同一笔赔案签批，验证付款一次及经办人。列出两轮加派单的实付总额及分类，核对默认 Luna 的实跑目标低于 $1。
- [ ] 保存每一页的明暗截图，交由一位未参与页面实现的新子代理，以资深产品设计师身份按品牌及需求第 8 点集中评审一次；修正发现并复验截图。更新 `docs/SPEC.md`、`docs/TECH_STACK.md`、`docs/PROCESS.md`、`docs/CODE_STANDARDS.md`、`docs/STATUS.md` 和 `data/README.md`。本分支规范现有 8 条，按 brief 第 7、13、14 条的意图明确 agent 分工、专用 client 与目标式指令。交付记录分别列出离线检查、实跑结果和无法验证的项。

## 评审时的测试基线

2026-11-20，在包含当前 Scoreboard 源码且无项目 `.env` 的临时副本运行现有检查。Python 179 项通过，覆盖率 95.07%；Vitest 25 项通过，语句覆盖率 93.76%。`npm run check` 到 Playwright 时默认测试端口已被占用。在副本改用三个空闲端口后单独运行 `npm run e2e`，10 个场景全部通过。以上是实施前的结果，本计划的功能验收均待执行。
