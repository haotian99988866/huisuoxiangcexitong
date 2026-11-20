# 前端移动端、Telegram 壳与通用组件

> 适用范围：本仓**所有带页面的项目**（新建默认按本文；存量逐步对齐）。
> 真源：`D:\project\_规范\`；各项目 `docs/规范/` 放副本。
> 参考实现：`mingxi-admin` / `country-agent` 的 Layout + `telegram-boot` / `tg-boot`。

---

## 1. 两类页面壳（先分清，再统一）

| 类型 | 项目例子 | 手机 | Telegram 内 |
|---|---|---|---|
| **工具 / 后台类** | mingxi-admin、country-agent、会所 miniapp | 必须适配 | **上下左右全屏**；固定顶栏 + 底部操作栏 |
| **对外官网类** | mingxi-shuihui | 必须适配 | 一般不进 Mini App；吸顶顶栏即可，**不强上**底栏 |

官网类若以后进 Telegram，仍按「工具类」补全屏壳，并在需求里写明。

---

## 2. Telegram 全屏（工具类硬性）

1. `index.html` 同步加载 Telegram WebApp SDK，再加载 boot 脚本（勿只靠 React `useEffect`）。
2. boot 顺序：`ready()` → `expand()` →（支持时）`disableVerticalSwipes` → `requestFullscreen()`；失败可短延迟重试。
3. 进入 Mini App 后给根节点打标：`html[data-twa='1']`，CSS 用它调顶栏/安全区。
4. 必须处理 `safe-area-inset-*`（刘海、底部 Home 条）；底栏高度含安全区。
5. viewport：工具类建议限制缩放（与现 admin/country 一致），避免软键盘把布局撑乱。

会所栈（JSX）可不迁 TS，但 **boot + 安全区 + 底栏行为** 须与本文一致。

---

## 3. 固定顶栏与底部操作栏（工具类）

| 区域 | 约定 |
|---|---|
| 顶栏 | 固定（`position: fixed` 或等价），高度用 CSS 变量（如 `--header-h`）；只放标题 / 关键状态 / 次要操作 |
| 底栏 | 固定；主导航或主操作按钮；断点与现网对齐，默认 **`max-width: 760px`** 以下显示（桌面可用侧栏/顶栏导航，但样式同源） |
| 内容区 | `padding-top` / `padding-bottom` 为顶栏+底栏+安全区留空，禁止内容被挡住 |

禁止各页私自再做一套「页面自己的底栏」抢位置；页内主 CTA 用统一 `Button`，需要贴底时走壳层槽位，不另起一套 fixed。

---

## 4. 报错文案（新口径）

| 层 | 约定 |
|---|---|
| 后端 | 稳定 **`code`** + 给人看的 **中文 `message`**（字段校验可带 `fields`） |
| 前端 | **默认直接展示后端 `message`**；`code` 只做分支（跳登录、冲突刷新等） |
| 兜底 | 无 `message` 时用简短中文兜底（如「操作失败，请稍后重试」），开发环境可 `console` 打出 `code` |
| 官网双语 | 中文站跟后端中文；英文站可再映射，映射表不得盖住中文站的后端原文 |

废弃旧说法：「错误只返回 code、前端 `errors.json` 映射才展示」。存量文档若仍写 `errors.json`，以本文为准逐步改。

---

## 5. 按钮样式统一（硬性）

### 5.1 只能用封装，禁止裸样式按钮

- 页面里 **禁止** 随手写 `<button className="xxx">` / 内联一套新按钮皮。
- 一律用项目内 `components/ui/Button`（或套件同步过来的同名组件）。
- 链接要长得像按钮时，用导出的 **`buttonClass(variant)`**，不要复制 CSS。

### 5.2 变体名固定（跨项目同名）

| variant | 用途 |
|---|---|
| `primary` | 主操作（提交、保存、确认） |
| `ghost` | 次要描边（取消、返回级） |
| `quiet` | 文字级弱操作 |
| `danger` | 破坏性操作（删除、注销） |

颜色走各项目 `tokens.css`（`--brand-*` / `--gold-*` 等），**类名与结构统一**，品牌色可不同。

### 5.3 结构与尺寸

- 根类名：`btn` + `btn--{variant}`；块级加 `btn--block`。
- 默认最小高度约 **40px**，圆角与间距用 token（`--radius-*`、`--space-*`），不要魔法数字散落页面。
- `disabled` / loading：统一降透明度并禁点；loading 若需要，在 `Button` 上加 `loading` prop，不要各页自己转圈。

### 5.4 底栏里的按钮

底栏导航/操作也用同一套 `btn` 视觉语言（可加 `btn--tab` 等**套件内**修饰类），禁止底栏另写一套完全不同的按钮皮肤。

---

## 6. 常用结构必须封装成组件（工具）

「出现第二次就抽组件」；下列 **默认就要有**（工具类项目）：

| 组件 / 工具 | 职责 |
|---|---|
| `Button` + `buttonClass` | 全部按钮与按钮貌链接 |
| `Field`（或等价） | 标签 + 控件 + 错误文案 |
| `Toast` / 全局提示 | 成功/失败反馈；少用 `alert` |
| `AppShell` / Layout | 顶栏 + 内容槽 + 底栏 |
| `telegram-boot`（或 `tg-boot`） | 全屏与 `data-twa` |
| `errorText(err)` | 从 API 错误取出中文 `message`（+ 兜底） |

允许各项目主题不同，**不允许**再复制第三套 Button/Field 逻辑。

共享方式（当前默认，不上强制 monorepo 包）：

1. 真源说明在本文；参考实现以 **mingxi-admin** 的 `components/ui` + Layout + boot 为准。
2. 新项目从参考实现拷贝后改 tokens；行为变更先改参考实现，再同步其它项目。
3. 以后若拷贝成本高，再抽 `packages/` 或 `_规范/前端移动套件/` 实体文件 + 同步脚本。

---

## 7. 新建 / 改版检查清单

- [ ] 手机宽度下无横向溢出；主操作可点、不被底栏挡住
- [ ] 工具类：Telegram 内四边全屏；`data-twa` 生效；安全区正确
- [ ] 顶栏、底栏样式与布局符合第 3 节
- [ ] 所有按钮走 `Button` / `buttonClass`，变体仅限第 5 节名单（新增变体须改本文）
- [ ] 报错展示后端中文 `message`
- [ ] Field / Toast / Layout / boot / `errorText` 已封装，无第二套平行实现

---

## 8. 例外

需求须写明：「本项目例外：……，原因：……」。未写则按本文。  
典型例外：纯营销落地页不做底栏；嵌入第三方只读页不做全屏。
