# 部署指南

## 当前 Windows 本机部署（固定 Cloudflare 隧道）

- 公网地址：`https://app.haotianzhushou.com/`。
- 命名隧道：`huisuo`，转发到 `http://127.0.0.1:4173`；前端通过 `/api` 代理本机 8000 端口。
- 隧道配置及凭据保存在当前用户的 `.cloudflared` 目录，不要提交凭据。
- 手动启动：在项目根目录执行 `powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start-local.ps1`。
- 脚本启动 API、机器人、已构建的前端预览和固定隧道；跳过已运行的实例，并检查本地及公网页面、数据接口。前端更新后需先在 `miniapp` 目录执行 `npm run build`。
- 当前电脑的用户启动目录已配置 `huisuo-services.cmd`，登录 Windows 后调用上述脚本。这不是系统服务，未登录时不会启动，也不负责进程崩溃后的自动重启。
- 启动和服务日志位于 `%LOCALAPPDATA%\Huisuo\logs`。MySQL84 已配置为自动启动的 Windows 服务。
- BotFather 的 Main Mini App 和机器人菜单网址应保持为上述固定地址。电脑关机、休眠或断网时，小程序仍不可用。

## 独立频道同步（需本机用户账号登录）

`bot/channel_sync.py` 是手动启用的独立进程，不会随现有 API/机器人自动启动，也不会读取私人聊天。固定监听已授权转载的来源 `@svip`，由现有机器人发布至 `@haotiancm99988866`；用户账号须订阅来源，机器人须有目标频道发帖权限。用户和机器人采用独立 MTProto 会话，不启动额外 Bot API 轮询。来源频道或消息启用保护内容时拒绝转载，不能绕过。

在项目根目录用 PowerShell 执行：

```powershell
bot\venv\Scripts\python.exe -m pip install -r bot\requirements-channel-sync.txt
# 仅首次配置；已有配置时不要覆盖
Copy-Item bot\.channel-sync.env.example bot\.channel-sync.env
# 在本机编辑 bot\.channel-sync.env，填入自己的 api_id / api_hash
bot\venv\Scripts\python.exe bot\channel_sync.py login
# 按终端提示自行输入手机号、验证码、两步验证密码；登录不会开始转发
bot\venv\Scripts\python.exe bot\channel_sync.py run
# 出现 READY 才表示开始监听；Ctrl+C 停止
```

API 配置文件、Telegram 登录会话禁止发到聊天、提交 Git 或上传服务器外的第三方。现有机器人 token 从本机 `bot/.env` 读取，不需要复制。会话、日志和 SQLite 发送记录存于 `%LOCALAPPDATA%\Huisuo\channel-sync`，应仅允许当前 Windows 用户访问；会话文件相当于账号登录权限，不是普通缓存，勿删除后重新登录来解决不明问题，可在 Telegram「设置 → 设备」撤销授权。

只接收每次服务就绪后到达的新消息，不扫描历史或补取停机期间的消息；但已经入队且尚未尝试发送的任务会在重启后继续。文字、图片、视频/音频和文件支持重新发布，相册按组发布；不支持的消息（如投票）明确记为失败。清理正文和媒体说明中指定的「关注大事件频道➡️ @Svip 投稿：@tx188」推广段、URL（包括隐藏链接的标签）及 Telegram 联系方式，不额外添加来源；原格式转为纯文字，媒体内容和文件名不做修改，图片或视频画面中的联系方式不会被清除。纯文字清理后为空则跳过，媒体说明为空仍发布媒体。

单实例锁避免同时运行两个监听进程，发送记录按来源/目标/消息组合去重；媒体仅临时落盘，用完清理。Telegram 限流明确等待；普通失败记录 `failed`，发送期间连接中断或进程退出标记 `uncertain`，不自动重发以免重复，需人工对照目标频道处理。这不是“绝不丢失/恰好一次”的承诺；停止进程后可检查日志或执行以下命令查看状态（运行期间也可查看）：

```powershell
bot\venv\Scripts\python.exe bot\channel_sync.py status
```

上线前必须完成真实账号登录、确认来源/目标及授权，并做真实新消息验收；单元测试不代表已连上 Telegram。电脑休眠、关机、网络断开或进程关闭会停止监听。

当前本机监听已切换为独立后台进程，并核实新的 `READY` 日志；关闭 VS Code 或原来的交互终端不影响该后台实例。当前用户启动目录已配置 `huisuo-channel-sync.lnk`，登录 Windows 后隐藏运行 `deploy\start-channel-sync.ps1`；脚本检查配置和已登录会话，已有监听进程则不重复启动，新实例最多等待 120 秒检查新的 `READY` 日志。启动结果写入 `%LOCALAPPDATA%\Huisuo\channel-sync\startup.log`，进程输出位于同目录的 `listener.out.log` / `listener.err.log`。这是用户登录自启，不是未登录也运行的系统服务，不提供崩溃自动重启；尚未通过真实重启验收。要取消自启，只删除用户启动目录中的 `huisuo-channel-sync.lnk`，不影响其他服务或当前运行实例。

不要再在终端运行第二份 `run`；需要手动后台启动时执行 `powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start-channel-sync.ps1`。查看 `%LOCALAPPDATA%\Huisuo\channel-sync\sync.log` 或运行 `status` 检查发送记录。停止后台时须先核实 `channel_sync.py run` 对应的具体进程 ID，并检查发送中的任务，再停止指定进程，不能按 Python 进程名批量结束，以免影响现有 API 和机器人。

## 小程序导航扩展

顶部固定显示会所、外送、观影、音乐及“全部”入口；全部功能面板按内容、便民服务、个人与帮助分组，仅显示已接入的页面。
模块名称、分组、常用入口及直达参数统一登记在 `miniapp/src/utils/modules.js`；新增页面仍需在 `App.jsx` 注册路由。
外送直达地址为 `/?module=delivery`，会所入口为 `/?module=venue`；原首页、观影和充值链接保持兼容。
浏览筛选与滚动位置仅在当前页面会话内保留，不缓存余额或身份数据；面板支持关闭按钮、遮罩、Escape 和 Telegram 返回键。
个人中心位于 `/personal`，支持 `startapp=personal` 直达；从全部功能的个人与帮助分组进入，可查看账户余额、最近 30 条充值申请及客服规则。余额和记录复用后端签名验证的账户接口，普通浏览器不展示私人账户数据。
技师预约消息包含可用的资料描述和频道照片原帖链接。Telegram 客户端可能不保留链接中的预填文字，详情页提供复制预约信息按钮；剪贴板不可用时显示只读文本供手动复制。KTV 页底部保留统一联系咨询入口。
联系链接先调用 Telegram 的 `openTelegramLink`，1.5 秒后页面仍可见时再使用 `tg://resolve` 兜底；直接使用原生协议的方案在用户真机上无法打开，已撤回。会所及外送预约消息优先放来源链接，缺少原帖时使用现有媒体接口的公开地址；描述按 Telegram 4096 字符上限截取。客人仍需手动点击发送，未自动通知管理员；客户端是否实际预填须在真机确认。

观影区全部影片与集数的播放链接统一附加 `t=1`，请求 Telegram 从第 1 秒播放；原有其他链接参数保留。《定风波》第 1 集已通过用户真机确认可直接进入播放，其他客户端行为仍由 Telegram 决定。使用 Telegram 原生播放器，退出播放后仍会返回频道，非小程序内播放。

## 图片加载

技师照片、外送图片和观影封面在首次请求后缓存到服务端本地；Windows 默认目录为 `%LOCALAPPDATA%\Huisuo\image-cache`（未设置时使用系统临时目录）。缓存以 Telegram 文件 ID 的哈希隔离，重启 API 后仍可复用，写入时清理超过 7 天或总量超过 256 MB 的旧缓存。同一图片的并发首次请求合并下载。
会所列表头像和外送列表、缩略图使用 `?thumb=1` 的最长边 480px WebP，放大及资料图片链接继续使用原图。观影使用 Telegram 已有封面并复用本地缓存。浏览器图片缓存仍为 private、5 分钟，支持 ETag/304；不对照片展示校验做旁路，也不改变视频流和 Range 请求。未缓存图片首次请求仍需要 Telegram 回源，不能保证所有图片首次打开即秒出。

## KTV 视频启动加速

KTV 包房列表和播放器交互保持不变。API 启动后通过单独的后台线程预取现有 KTV 视频，每轮完成后等待 60 秒再检查新文件，不占用 API 请求线程下载缓存。
视频缓存保存在 `%LOCALAPPDATA%\Huisuo\ktv-video-cache`（未设置时使用系统临时目录），以 Telegram 文件 ID 的哈希隔离，重启后可复用；总量上限 512 MB，单文件上限 32 MB。完整下载并校验声明大小及 MP4/WebM 容器标识后才原子发布，空间不足时淘汰旧文件，不能缓存的文件明确记录错误并保留原 Telegram 转发路径。
视频接口仍先查询数据库确认包房视频存在；命中本地缓存后支持 Range/206、拖动进度、HEAD 与 ETag/304，响应头 `X-Media-Cache` 为 `HIT`。缓存未就绪时为 `MISS`，继续使用原流式转发，不阻塞等待整段下载；转发结束或客户端断开时关闭上游连接。新增视频首次缓存完成前仍可能等待 Telegram 回源，客人实际播放速度仍受服务器上传和客人网络影响。

## 展示服务租赁基础（暂未开通真实租户）

第一版共用 `haotianzhaopian_bot`，提供租户品牌名、联系名、Telegram 联系用户名和会所 ID 白名单；租户仅有会所、观影与联系入口，不含外送、个人中心、钱包或充值。不做租户自助后台、自动收月费或扣款；平台运营者人工收费、核实授权后开通和续期。主入口原有功能保持不变。

### 本机管理员管理

在 `bot` 目录运行（Windows 使用 `.\venv\Scripts\python.exe`）：

```powershell
# 仅创建表，不开通租户；API 启动时也会初始化表
.\venv\Scripts\python.exe tenants.py init
.\venv\Scripts\python.exe tenants.py list

# 示例命令，不会自动执行。先创建草稿，不可对外访问
.\venv\Scripts\python.exe tenants.py create partner-one --brand "合作方品牌" --contact-name "合作方客服" --contact-username "partner_contact" --venues "1,2,3" --status draft

# 人工核款后，用完整配置更新并启用；到期时间一律为 UTC，不含时区
# 下例日期仅示范格式，实际填写未来日期
.\venv\Scripts\python.exe tenants.py update partner-one --brand "合作方品牌" --contact-name "合作方客服" --contact-username "partner_contact" --venues "1,2,3" --status active --expires "2030-01-01T00:00:00"

# 暂停时同样提供完整配置，省略到期时间即清空到期时间
.\venv\Scripts\python.exe tenants.py update partner-one --brand "合作方品牌" --contact-name "合作方客服" --contact-username "partner_contact" --venues "1,2,3" --status suspended
```

`create` 不覆盖已有租户；`update` 不会隐式创建租户。续期用 `update` 设置新的未来到期时间并保持 `active`。联系人不带 `@`，可选择当前启用的会所 ID 1–6、外送 ID 7–10。外送默认关闭：只有明确列在 `--venues` 或管理台勾选保存的外送项目才开放，现有租户权限不会自动扩展。无需修改表结构，仍以 `venue_ids` 记录各项目授权并纳入版本校验与审计。第一版不支持自定义头像或租户公告，公告显示品牌与信息服务说明。

### 专属入口与边界

- Telegram 入口：`https://t.me/haotianzhaopian_bot?startapp=tenant_partner-one`。
- 网页入口：`https://app.haotianzhushou.com/?tenant=partner-one`；直接进入观影为 `/movies?tenant=partner-one`。
- 导航、接口及媒体链接保留租户标识；预约、复制提示和 KTV 预订使用该租户联系人。没有有效租户配置时阻止访问，不退回昊天主入口。配置不返回账务、管理员或客户数据。
- 管理台表单分为会所权限、外送权限两个分组，外送按项目逐项勾选。未授权外送时隐藏导航并阻止直达；授权后仅返回选中项目，外送图片/视频在读取缓存或回源前按所属项目校验。仅外送租户首页进入外送模块。原有成年过滤、白晚班展示和钱包禁用规则保持不变。
- API 每个带租户标识的请求检查启用状态、UTC 到期时间及会所白名单；媒体命中本地缓存也先检查范围。租户响应禁止浏览器/CDN 缓存，避免过期配置或其他租户配置复用，但服务器本地公共媒体缓存仍共享。打开页面每 60 秒复核租户状态；故障、暂停或到期显示不可用提示。
- 这是**公开内容的品牌化展示订阅**，专属链接不是登录凭证或内容付费墙。主入口、公共频道和未携带租户标识的原公开 API 仍存在；用户可自行离开租户入口访问主平台。不能宣称租户白名单可阻止用户通过公共来源查看其他会所，也不能把去掉租户标识理解为取得管理员权限。租户入口不提供任何私人账户或钱包数据；主平台钱包仍使用原 Telegram 签名身份校验。
- 共用机器人时机器人名称和 Telegram 框架品牌不会独立；影视、照片和个人资料商业展示须有相应授权。正式收费扩展前应另行落实内容授权、托管稳定性、备份与服务条款。新表 `display_tenants` 应纳入数据库备份。

## 音乐播放器（Telegram 频道音源，主入口试运行）

平台顶部一级导航「会所 / 外送 / 观影 / 音乐 / 全部」中直接点击「音乐」，或通过 `https://t.me/haotianzhaopian_bot?startapp=music` 进入。「全部」仍保留完整功能入口。租户不显示音乐，带租户标识的歌库请求和音频请求均拒绝；不自动扩展现有租户权限。平台音乐是公开试听，不是付费墙，不做用户上传、下载按钮或付费歌曲解析。只录入自有或已取得公开展示/播放授权的音频。

提供歌名/歌手/专辑搜索、播放暂停、上一首/下一首、进度拖动和内部跨页迷你播放器。支持顺序循环、随机播放、单曲循环；点击列表歌曲或「播放全部」后，在该列表内切歌。音乐播放时暂停页面其他音视频，页面视频开始播放时暂停音乐，防止 KTV 和音乐同时出声。iPhone 使用手机音量键；其他支持的浏览器提供音量滑块。锁屏、切后台、客户端省电或关闭小程序后无法保证继续播放，不承诺系统级后台播放器。歌曲下架阻止新的列表/音频请求，不强行清除客户端已经缓冲的音频。

音乐页使用「发现 / 我喜欢 / 最近 / 歌单」分区，搜索在当前歌曲列表内筛选。浏览页不显示大播放器，点歌直接播放、不自动弹窗，底部迷你播放器显示状态和进度；点击标题在当前页面上打开独立完整播放界面，收起不会暂停、跳转或改变列表页码/滚动位置，在其他内部页面同样可展开。完整播放界面提供渐变唱片背景、进度拖动、收藏、播放模式和队列，音量及停止操作放入更多设置。拖动进度松手后才提交，避免连续网络保存。

完整播放器固定在视口内，外层禁止横向和纵向滚动，按可用高度缩放唱片与控件，并避让 Telegram 顶部及底部安全区域；极短屏隐藏装饰唱片，优先保证播放、进度和底部工具可用。长歌名最多显示两行，歌手/专辑单行省略。队列和更多设置切换为独立面板，仅面板内容内部可纵向滚动，不撑长播放页；加载及错误反馈在独立浮层中显示，长提示可在提示区域内查看，不改变播放布局。

歌曲「更多」先列播放、下一首播放、收藏、添加到歌单及移出操作，点击添加后才显示歌单选择，选中成功自动关闭并提示。也可新建歌单并同时添加该歌曲。歌单页的「添加歌曲」打开带搜索和分页的选择面板，逐首添加后保持面板，完成后返回原歌单。创建、收藏、改名、移除等操作有结果提示。歌单卡片支持播放全部、分享和管理/确认删除。完整播放界面及底部面板均支持关闭按钮、Telegram 返回和键盘 Escape，不新增外部封面请求或改变音源权限。

音乐页移除装饰标题及刷新按钮，搜索框尾部提供「搜索」确认按钮，点击或键盘搜索键提交关键词并收起键盘，输入过程中不改变结果；清空立即恢复当前分类全部歌曲。输入框不单独显示蓝色轮廓，改为整个搜索栏的聚焦边框，保留键盘可见反馈并使用 16px 字号避免 iOS 聚焦缩放。「会所 / 外送」等模块导航、搜索框和音乐分类作为整体吸顶固定（仅音乐页），全屏公告随导航一起保留安全区域；翻页回到歌曲区域时按实际吸顶高度避让，不遮挡标题。歌曲列表固定每页 20 首，不随屏幕或键盘高度改变；不超过 20 首不显示分页，超出才在列表末尾显示单行「上一页 / 当前页输入框 / 总页数 / 跳转 / 下一页」，分页随内容滚动、不固定在屏幕底部。歌单添加面板仍每页 12 首。

确认搜索和切换分类回到第一页，列表缩短时自动收敛到有效页。搜索覆盖当前分类全部歌曲，播放全部/切歌仍按完整筛选列表，不受浏览页码限制；翻页不中断播放，并滚回歌曲区域开始处。内部页面返回音乐时保留本次浏览分类、已提交关键词、页码及位置，刷新不保存浏览状态。队列仅本次运行有效，不修改歌单；「下一首播放」去重并优先于随机选曲，完整播放器中可选择队列歌曲或移除非当前歌曲，切换新的播放列表会清除优先队列。单曲循环仍以循环当前歌曲为准，手动下一首可消费优先队列。本版为前端分页，歌库接口的 500 首上限不变。

个人功能使用服务器校验的 Telegram initData 绑定账号，不接受客户端传入用户编号：收藏最多 500 首，最近播放保留 50 首，账号最多创建 50 个歌单、每单 200 首、名称 1–80 字。播放模式和上次歌曲/进度保存至 `music_profiles`；进度每 10 秒及暂停、拖动、切后台时尝试保存，异常关闭可能丢失最后几秒。重开音乐页仅恢复歌曲和进度，仍须点击播放；停止播放会清除记忆。普通浏览器可试听公共歌库或分享歌单，但不能保存个人数据。

歌单存储在 `music_playlists`，创建者可重命名、添加/移除歌曲和删除，带 revision 防止跨设备旧修改覆盖新修改。通过 `https://t.me/haotianzhaopian_bot?startapp=playlist_<随机32位标识>` 分享；持链接者可免登录查看原歌单最新内容、播放和继续分享，登录者可保存独立副本，不能修改别人的原歌单。不展示创建者 Telegram 身份；链接不是私密访问控制，勿在名称中填写隐私信息。删除歌单会让分享链接失效。下架歌曲在列表中隐藏，保存歌单时剔除失效歌曲。API 启动幂等创建这两张表，部署时重启 API；备份时同时备份个人音乐与歌单表。租户入口继续禁止所有音乐相关请求。

音乐来源固定为 `@haotiandkhy99988866`（频道 ID `-1002368924969`，「音乐收藏与搜索」），机器人为 `@haotianzhaopian_bot`。机器人必须在该频道拥有接收频道帖子的权限。发布或编辑音频消息时，机器人读取 Telegram 音频标题/歌手；作为文件发送时以文件名作为歌名，缺歌手显示「未知歌手」。其他频道不录入，视频、语音不是音乐来源。相同频道消息重复/编辑时更新原记录，不增加重复歌曲，也不把已手动下架的歌曲自动上架。频道仅作为音源，客人无需进入频道，接口不会下发频道或带 Token 的 Telegram 文件地址。使用频道代表管理员确认其中歌曲已获公开播放授权。

歌曲按「歌名＋歌手」自动去重（不比较专辑或文件内容）：NFKC 统一全半角，忽略大小写、首尾及多余空白；`<unknown>`、`unknown` 和缺失歌手统一视作「未知歌手」。同名不同歌手保留；同名同歌手即使音频版本不同也只保留一首，若需区分须在歌名中标注现场版等。数据库唯一键加跨进程录入锁防止并发重复。重复新帖直接复用已有歌曲，不下载第二份、不替换首条音源，也不恢复已下架歌曲；原始音源消息编辑仍能更新歌曲，编辑后与另一首同名同歌手则合并。备用本机导入同样去重。

首次迁移合并旧数据时，保留最早编号歌曲的音源、元数据和上架状态；收藏、最近播放、歌单自动迁移到保留编号并去重，歌单 revision 递增，分享链接不变。被合并歌曲的旧编号保留为别名，旧音频链接转向保留音源；该音源下架时旧链接也拒绝播放。合并导致音源变化时进度重置为零，避免恢复到不对应的位置。不会自动删除频道消息或旧本机缓存文件。部署此次升级先停止 API 和机器人，执行 `music.py init` 完成迁移，再启动两者，避免旧版写入绕过去重；后续重复初始化幂等。

音频先完整下载并校验大小和基本文件头，再录入数据库，避免让客人播放半文件。由于当前使用 Telegram 云端 Bot API，单首限制 **20 MB**，支持 MP3/M4A/WAV/OGG，iPhone 推荐 MP3/M4A。文件头检查不等于完整解码校验，录入后仍须试听。录入失败会记录错误类别并私信平台管理员，修复后编辑/重新发布歌曲重试；机器人不会自动获取入群前的历史，旧消息需要编辑或重新发布。频道删除消息不会自动下架歌曲，需用本机 `hide` 命令下架。下载时临时失败不发布半文件；数据库写失败可能留下未引用缓存，可在停服备份后人工清理。

本机缓存目录为 `%LOCALAPPDATA%\Huisuo\music`，可用 `MUSIC_LIBRARY_DIR` 指定固定目录（API、机器人与本机命令须一致，修改后重启 API 和机器人）。缓存已存在直接支持 HEAD、Range/206、拖动进度；缓存丢失时尝试从 Telegram 下载，首次会等待完整下载。缓存目录总量限制 1 GB，达到后明确通知管理员，不自动删除正在使用的歌曲。数据表 `music_tracks` 与该目录建议一起备份；数据库的频道音源可在 Telegram 文件仍可获取时恢复缓存，旧本机导入歌曲仍必须备份文件。不要把音频文件放进 Git。电脑关机或服务停机即无法播放。歌库最多 500 首，超过明确报错，需下架或升级分页。

在 `bot` 目录执行：

```powershell
.\venv\Scripts\python.exe music.py init
.\venv\Scripts\python.exe music.py list
# 可选本机导入备用：单文件最多 128 MB，仍需确认授权；原音频不移动、不删除
.\venv\Scripts\python.exe music.py add "D:\AuthorizedMusic\song.mp3" --title "歌名" --artist "歌手" --album "专辑" --rights-confirmed
# 下架/重新上架（文件保留便于恢复）；list 仅列出当前上架歌曲
.\venv\Scripts\python.exe music.py hide 1
.\venv\Scripts\python.exe music.py show 1
```

## 手机运营管理台（第一版）

联系人跳转默认使用 Telegram 官方 `openTelegramLink`。当前按真机对比测试需求，iPhone 会所页通用联系区域提供两个独立按钮：「直接联系」仅调用内部跳转，「通过网页联系」使用官方 `openLink(url, { try_browser: 'safari' })`。iPhone 当前客服联系链接不自动延时中转，也不叠加协议兜底，避免两种测试互相干扰。网页方式可能先经过 Safari 再进入聊天，旧客户端是否采用指定浏览器由 Telegram 决定。预约 `text` 参数原样保留，但自动带入聊天与否需真机确认；独立网页按钮只打开聊天、不附加预约资料。安卓、电脑及观影频道跳转保持原样。浏览器模拟只能验证调用路径，不能证明 iPhone 原生聊天已打开。

平台主入口的「个人中心」在账户通过 Telegram 签名验证且 ID 位于 `ADMIN_USER_IDS` 时显示「运营管理台」。也可由管理员直接打开 `https://t.me/haotianzhaopian_bot?startapp=admin`，页面路由为 `/admin`；普通浏览器没有 Telegram 签名身份会明确拒绝，不能仅凭网址或客户端自报 ID 获取数据。现有机器人 `/admin`、`/wallet` 和本机租户命令继续保留。

- 范围：租户总数、启用数、7 天内到期数、已到期数、草稿/暂停数、待核充值数量，以及租户创建、编辑、续期、暂停、专属入口复制。第一版不提供充值审核、广播编辑、服务器进程探测或租户自助后台。待核充值仅显示数量，不执行资金操作。
- 到期时间在表单按操作手机的本地时区输入、展示，提交时转成明确的 UTC `...Z` 时间，数据库仍使用 UTC。启用必须设置未来到期时间；新建默认草稿。不会自动按月扣费或自动加一个月，人工收款后设置新的到期时间并选择启用。
- 所有保存均先展示品牌、联系人、会所、状态与到期时间确认；入口标识创建后不能在网页改名。保存不允许把不存在的租户隐式创建或覆盖已有标识。遇到网络异常请先刷新核对状态与记录，前端不会自动重试写入。
- 管理 API 每次校验 Telegram 签名、授权时间和服务端管理员白名单；即使管理员使用 `tenant_...` 签名会话或带租户请求标识也拒绝管理操作。管理员响应 `private, no-store`，无独立管理密码、无管理员 ID 客户端信任。
- 更新使用配置版本摘要和数据库行锁防止覆盖并发修改，冲突返回 409 并提示刷新。`tenant_admin_audit` 与租户配置在同一事务中写入，记录操作者、来源、前后配置及时间，审计失败则整体回滚。本机 CLI 同步记录来源 `cli`；此前已存在的租户不补造历史记录。表应纳入数据库备份，不随 48 小时业务数据清理删除。
- 页面显示最近 30 条操作元信息，完整前后配置保存在数据库。租户列表上限 200，超过明确提示升级分页，不静默隐藏。概览按刷新时的数据统计，暂停和到期仍由原租户访问检查执行。

## 充值模块当前状态

小程序 `/recharge` 和机器人主菜单已增加充值中心入口，支持 `startapp=recharge` 直达。
Premium 和话费均未开放商品下单，不会自动交付。余额充值地址须管理员自行配置。
余额模块支持 Telegram 签名身份校验、TRC20 充值申请、管理员人工核款入账及充值记录。未配置有效收款地址时禁止提交申请。
管理员私聊机器人发送 `/wallet` 查看待审核申请及命令；`/wallet address 地址` 配置收款地址；
`/wallet approve 申请编号 实际到账金额` 生成二次确认按钮，独立核对链上转账后点击才入账；
`/wallet reject 申请编号` 拒绝申请。用户提交 TXID 后不会自动入账，也不会自动通知管理员，管理员需主动查看 `/wallet`。
同一 TXID 只能提交一次（被拒绝后也不能重复提交，误报请联系管理员）。入账使用数据库事务、行锁和唯一流水防止重复记账。
1 USDT 实际到账计 1 U；余额仅供后续话费消费，不用于 Premium。无自助提现、无自动链上付款、无通用扣款功能，话费扣款和失败退款待商品规则确认后实现。数据库备份须包含所有 `wallet_` 表。
后续开放话费前需要确认运营商、面额、售价、收款币种及网络、处理时限和退款规则，再实现订单、凭证及管理员核款和履约流程。
Premium 需要接入实际可用的自动交付渠道，并确认 Telegram Stars 支付与退款方案，不复用话费的虚拟货币人工收款流程。

## 可选：Oracle 永久免费服务器部署

结构：一台服务器同时运行 MySQL、API、Telegram 机器人和前端，Caddy 提供免费 HTTPS。

## 1. 创建 Oracle 免费服务器

1. 注册 <https://www.oracle.com/cloud/free/>（需要银行卡验证，不扣费）。
2. 控制台 → Compute → Instances → Create instance：
   - Image：**Ubuntu 22.04**
   - Shape：**Ampere（ARM）VM.Standard.A1.Flex**，选 2 OCPU / 12GB 内存（标有 Always Free）
   - 下载 SSH 私钥并保存好
3. 在实例所属子网的 Security List 里添加入站规则：TCP **80** 和 **443**（来源 0.0.0.0/0）。
4. 记下实例的公网 IP。

## 2. 申请免费域名

1. 打开 <https://www.duckdns.org>，用 GitHub 账号登录。
2. 创建一个子域名（如 `myclub`），IP 填服务器公网 IP。得到 `myclub.duckdns.org`。

## 3. 登录服务器并拉取代码

```bash
ssh -i 私钥文件 ubuntu@服务器IP
sudo iptables -I INPUT -p tcp -m multiport --dports 80,443 -j ACCEPT
sudo apt-get install -y iptables-persistent   # 提示保存规则时选是
sudo git clone https://github.com/haotian99988866/huisuoxiangcexitong.git /opt/clubapp
```

> Oracle 的 Ubuntu 镜像自带防火墙规则，第二行用于放行 80/443，两处（安全列表和系统防火墙）都要开。

## 4. 一键安装

```bash
cd /opt/clubapp
sudo DOMAIN=myclub.duckdns.org BOT_TOKEN='机器人令牌' bash deploy/setup.sh
```

脚本会安装 MySQL、Node、Caddy，创建数据库和随机密码（写入 `/opt/clubapp/bot/.env`，权限 600），安装依赖、构建前端并注册两个常驻服务。

## 5. 导入数据并启动

在**自己电脑**上导出数据库（密码会提示输入），再上传：

```powershell
& 'C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqldump.exe' -u root -p --single-transaction --default-character-set=utf8mb4 --no-tablespaces venue_db -r venue_db.sql
scp -i 私钥文件 venue_db.sql ubuntu@服务器IP:/tmp/
```

在服务器上导入并启动：

```bash
sudo mysql venue_db < /tmp/venue_db.sql
sudo rm /tmp/venue_db.sql
sudo systemctl start clubapp-api clubapp-bot
sudo systemctl status clubapp-api clubapp-bot --no-pager
```

## 6. 检查与切换

- 浏览器打开 `https://myclub.duckdns.org`，应能看到页面。
- 验证接口：`curl -s -X POST https://myclub.duckdns.org/api/webhook -H 'Content-Type: application/json' -d '{"action":"get_venues"}' | head -c 200`
- **关闭自己电脑上的机器人**（同一个机器人令牌不能两处同时运行）。
- 在 BotFather 里：`/mybots` → 选机器人 → Bot Settings → Menu Button（或 Configure Mini App）→ 网址改为 `https://myclub.duckdns.org`。

## 日常运维

```bash
sudo journalctl -u clubapp-bot -f      # 查看机器人日志
sudo journalctl -u clubapp-api -f      # 查看 API 日志
cd /opt/clubapp && sudo -u clubapp git pull
sudo -u clubapp bash -c 'cd miniapp && npm ci && npm run build'
sudo systemctl restart clubapp-api clubapp-bot
```

每日备份数据库（可放入 crontab）：

```bash
sudo mysqldump --single-transaction venue_db | gzip > ~/venue_db_$(date +%F).sql.gz
```

## 注意

- 机器人只能收到启动之后的频道消息，迁移期间尽量缩短两边都不运行的时间。
- `.env` 里的令牌和密码不要提交到 Git。
- Oracle 免费实例长期闲置可能被回收，保持服务运行并定期登录即可。
