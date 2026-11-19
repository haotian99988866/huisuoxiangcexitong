# 多 AI 协作与交接规范

> 解决的问题：**中途换人（换 AI）时，规矩不能丢。**
> 适用：Copilot、Cursor、Claude Code、Codex、Cline、以及我在同一会话里派生的子智能体。

---

## 0. 为什么单靠「写在文档里」不够

三个现实：

1. **每个 AI 读的文件不同**：Copilot 读 `.github/copilot-instructions.md`，Cursor 读 `.cursor/rules/*.mdc`，Claude Code 读 `CLAUDE.md`，Codex 与多数通用 Agent 读 `AGENTS.md`。你把规则写在一处，另一处就是空白。
2. **AI 默认不读你的历史**：它从零开始，最省事的做法是「按自己的习惯写」，那就是绕开你的分层。
3. **规则会漂移**：同一个文件被三个 AI 各改一次，最后谁也不认识。

解法不是「写得更详细的文档」，而是**一份真源 + 多个薄壳 + 可检查的落地**。

---

## 1. 三个原则

| 原则 | 含义 |
|---|---|
| **单一真源** | 规则只在一个地方写、一个地方改；其余都是它的投影 |
| **薄壳** | 各工具的入口文件只放「指针 + 硬红线」，不写细则，避免三份内容打架 |
| **可检查** | 每条红线都要有办法验证（lint、脚本、CI），否则等于没有 |

---

## 2. 真源与薄壳

### 2.1 真源（唯一）

```text
AGENTS.md                          # 索引 + 10 条硬红线（≤80 行）
.github/instructions/
├── frontend.instructions.md       # 前端细则（applyTo: frontend/src/**）
├── backend.instructions.md        # 后端细则（applyTo: backend/**）
├── admin.instructions.md          # 管理后台特有（认证/审计/数据边界）
└── review.instructions.md         # 收尾自审
docs/约束规则.md                   # 业务约束（人读，AI 按需查）
```

**只有这四个 instructions 与 AGENTS.md 可以手改。**

### 2.2 薄壳（生成物，禁止手改）

```text
.github/copilot-instructions.md    # Copilot 入口
CLAUDE.md                          # Claude Code 入口
.cursor/rules/project.mdc          # Cursor 入口
.windsurfrules                     # Windsurf 入口（如需）
```

每个文件头部必须有：

```markdown
<!-- 本文件由 tools/sync-rules.ps1 从 AGENTS.md 生成，请勿手改。
     要改规则请改 AGENTS.md 或 .github/instructions/，然后重新生成。 -->
```

内容 = 硬红线（复制）+ 一句「细则见 `.github/instructions/`」。

### 2.3 生成器

`tools/sync-rules.ps1`：

1. 读 `AGENTS.md` 里 `<!-- BEGIN HARD-RULES -->` 与 `<!-- END HARD-RULES -->` 之间的内容
2. 按模板写入各薄壳文件（带生成标记）
3. 若发现薄壳被手改（内容与生成结果不一致），**报错并提示先提交或还原**

配合 `docs/verify.md` 加一步：`tools\sync-rules.cmd -Check`，不一致即失败。

> 这一条能杜绝「三个 AI 各自维护一份规则」的最坏情况。

---

## 3. 任何 AI 进场，必须做的四步

不管是谁（包括我），拿到这个项目的第一件事固定为：

1. **读 `AGENTS.md`** —— 知道有哪些规则、契约在哪、清单在哪
2. **读 `docs/status.md`** —— 知道现在做到哪、下一步是什么、有什么已知问题
3. **读 `contracts/openapi.yaml` 的目录结构** —— 知道有哪些接口（不必细读字段）
4. **读 `docs/verify.md`** —— 知道改完要跑什么

然后**用一句话复述**：「我理解当前在 X 阶段，下一步是 Y，本次要做 Z」。复述不对，先别写代码。

### 我（主智能体）派生出的子智能体

子智能体同样受这套规矩约束。我派活时必须：

- 把 `AGENTS.md` 的硬红线**原样**写进它的任务描述（它可能读不到文件）
- 明确它**能改哪些文件**、不能碰哪些
- 要求它返回「改了什么 + 怎么验证」

---

## 4. 交接：离场必须留的四样东西

任何一个 AI 结束工作前，必须留下：

| # | 留下什么 | 写在哪 |
|---|---|---|
| 1 | **进度**：做完了什么、没做完什么 | `docs/status.md` |
| 2 | **验证**：跑了什么命令、实际输出 | 对话里 + `docs/status.md` 摘要 |
| 3 | **未做的说明**：为什么没做（不是漏了） | 对话里 |
| 4 | **本次身份**：哪个 AI、什么时候 | `docs/status.md` 的「最近交接」段 |

`status.md` 建议固定一段：

```markdown
## 最近交接
- 2026-11-19 04:20 · DSH(deepseek) · P0 完成后台骨架与登录，审计未接入
- 下一步：接入审计中间件，然后官网侧加 3 列迁移
```

**没有这段，下一个 AI 就要靠猜。**

---

## 5. git 是协作的底座（强烈建议启用）

现在 `mingxi-shuihui` 还没建 git 仓库。**多 AI 协作时，没有 git 就等于没有刹车**：

| 场景 | 有 git | 没 git |
|---|---|---|
| 另一个 AI 改坏了 | `git diff` 立刻看见，`git checkout` 回滚 | 只能靠备份，或者认了 |
| 两个 AI 同时改一个文件 | 冲突可见、可解决 | 后写的覆盖先写的，无声无息 |
| 想知道「谁改的、为什么」 | `git log` | 无从查起 |
| 交接时对方要了解现状 | `git status` + `git log -5` | 只能读 status.md 猜 |

**建议**：每个项目 `git init`，并且规定：

- 规则文件、契约、迁移脚本的改动**必须单独提交**，提交信息写清原因
- AI 每完成一个阶段提交一次，不攒着
- `docs/status.md` 跟着代码一起提交

---

## 6. 并发：两个 AI 同时在场怎么办

现实里会出现：你在 Cursor 里让一个 AI 改前端，同时让我改后端。

| 规则 | 说明 |
|---|---|
| **一人一块地** | 开工前说清楚「你只改 `frontend/`，我改 `backend/`」，不交叉 |
| **不共享长任务** | 不要两个 AI 同时改同一个页面/同一个 service |
| **契约先行者优先** | 谁要改 `openapi.yaml`，先说；另一方等契约稳定再动 |
| **迁移独占** | 同一时间只允许一个 AI 加 Alembic 迁移（否则 revision 链会断） |
| **收工即提交** | 谁先做完谁先提交，减少冲突窗口 |

冲突了怎么办：**以 `openapi.yaml` 和测试的结论为准**，不喜欢的实现重写，但契约不能各写一套。

---

## 7. 怎么发现「换了 AI 但没守规矩」

新 AI 最典型的 7 个违规信号，对照代码就能看出来：

| 信号 | 说明 |
|---|---|
| 页面里出现 `fetch` | 违反分层（数据获取必须在 `hooks.ts`） |
| 路由里出现业务 `if` | 违反分层（判断应在 `services/`） |
| `api/types.ts` 被手改 | 明确禁止，它是生成物 |
| 新增了没在 `pyproject.toml` 里的依赖 | 违反「新增依赖先说明」 |
| 迁移脚本没有 `downgrade` | 违反迁移对称 |
| 组件里出现中文字符串 | 违反文案集中 |
| 出现 `print()` / 裸 `except` | 违反后端风格 |

**自动化检查**（建议做成本地脚本 `tools/check-rules.cmd`）：

```powershell
# 1. 分层红线
Select-String -Path frontend\src\pages\*\index.tsx -Pattern "fetch\(" 
Select-String -Path backend\src\app\routers\*.py -Pattern "if .*==" 

# 2. 生成物是否被手改
npm run generate:api; git diff --exit-code frontend\src\api\types.ts

# 3. 薄壳规则是否与真源一致
tools\sync-rules.cmd -Check

# 4. 迁移是否可逆
alembic upgrade head; alembic downgrade base; alembic upgrade head
```

---

## 8. 给「外来 AI」的一段话（可直接粘给它）

> 本项目有既定规矩，**开始前必须先读三个文件**：`AGENTS.md`（规则索引与红线）、`docs/status.md`（当前进度）、`docs/verify.md`（改完怎么验证）。
>
> 硬性要求：
> 1. 接口以 `contracts/openapi.yaml` 为准，改接口先改契约，再跑 `npm run generate:api`；`api/types.ts` 是生成物，不许手改。
> 2. 前端页面只组合结构，数据获取写在同目录 `hooks.ts`；禁止在 `pages/**/index.tsx` 里出现 `fetch`。
> 3. 后端 `routers/` 只收参→调 service→返回；业务逻辑一律在 `services/`。
> 4. 数据库变更走 Alembic，`upgrade`/`downgrade` 对称。
> 5. 配置只从 `core/config.py` 读，禁止硬编码 URL/端口/密钥。
> 6. 新增依赖前先说明理由。
> 7. 改完运行 `docs/verify.md` 里的命令，把**实际输出**贴出来；只说「已通过」不算。
> 8. 结束时更新 `docs/status.md`，写清「做了什么 / 没做什么 / 下一步」。
>
> 不确定的地方**先问，不要猜**；跨 3 个文件或涉及契约/迁移的改动，先给方案再动手。

---

## 9. 落地清单（要建哪些东西）

| # | 文件 | 作用 |
|---|---|---|
| 1 | `AGENTS.md` 加 `<!-- BEGIN HARD-RULES -->` 标记 | 给生成器用 |
| 2 | `.github/copilot-instructions.md`（改为生成物） | Copilot 入口 |
| 3 | `CLAUDE.md`（生成物） | Claude Code 入口 |
| 4 | `.cursor/rules/project.mdc`（生成物） | Cursor 入口 |
| 5 | `tools/sync-rules.ps1` + `.cmd` | 生成与校验 |
| 6 | `tools/check-rules.cmd` | 违规信号扫描 |
| 7 | `docs/status.md` 加「最近交接」段 | 交接记录 |
| 8 | `git init` + 首次提交 | 协作底座 |

---

## 10. 待你确认

| # | 问题 | 我的建议 |
|---|---|---|
| 1 | 要不要做「生成薄壳」这套？还是手工维护两三份入口文件？ | **做生成**，手工维护必然漂移 |
| 2 | 会给别的 AI 用 Cursor / Claude Code 吗？还是只用 Copilot + 我？ | 决定要生成哪几个薄壳 |
| 3 | 现在给 `mingxi-shuihui` 和 `mingxi-admin` 做 `git init` 吗？ | **建议做**，多 AI 协作没有 git 很危险 |
| 4 | 允许两个 AI 同时在同一个项目里干活吗？ | 建议不允许同时改同一目录 |

第 3 条我认为是这批里最该做的——**它一条就覆盖了「改坏了怎么回滚」和「谁改的」两个问题**。
