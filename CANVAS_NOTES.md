# Qwen 画布面板（独立项目）

面板 = 「填参数出图 + 在图上框选写建议 + 历史管理」的那层 WebUI，**与模型/ComfyUI 解耦**：
不 import ComfyUI 的任何东西、不用它的 venv、不假设它就在隔壁。全部实测记录（2026-09-21）。

## 与 ComfyUI 的唯一接缝

两个接触点：**ComfyUI 的根目录**（读 `output\`、写 `input\`）和 **HTTP `127.0.0.1:8188`**。
根目录写在同目录 `canvas.json`，所以面板可以放任何地方、指向任何一份 ComfyUI（同机）：

```json
{ "comfy_root": "../qwen-image-2.1/ComfyUI", "comfy_autostart": true }
```

| canvas.json | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `comfy_root` | `QWEN_COMFY_ROOT` | `<面板>\ComfyUI` | ComfyUI 根目录；**相对路径按面板目录解析** |
| `output_dir` | `QWEN_OUTPUT_DIR` | `<comfy_root>\output` | 历史图 / 底图来源 |
| `input_dir` | `QWEN_INPUT_DIR` | `<comfy_root>\input` | 底图 / 蒙版 / 参考图中转 |
| `comfy_python` | `QWEN_COMFY_PYTHON` | `<comfy_root>\.venv\Scripts\python.exe` | 拉起 ComfyUI 用的解释器 |
| `comfy_log` | `QWEN_COMFY_LOG` | `<comfy_root>\qwen_server.log` | 拉起时它的 stdout |
| `comfy_cmd` | — | `["main.py","--listen","127.0.0.1","--port",<comfy_port>]` | 相对 `comfy_root` 执行 |
| `comfy_autostart` | `QWEN_COMFY_AUTOSTART` | `true` | `false` = 面板只当前端，绝不自己拉服务 |
| — | `QWEN_CANVAS_PORT` | `8189` | 面板端口 |
| — | `QWEN_COMFY_PORT` | `8188` | ComfyUI 端口 |

实测（2026-09-21）：`QWEN_COMFY_ROOT` 指向一个不存在的目录 + `QWEN_COMFY_AUTOSTART=false` 起第二个实例（8190），
页面/`/vendor` 正常、`/api/state` 报 `autostart:false`、`recent:[]`，不因"找不到 ComfyUI"退出；
改完配置后从新目录起，`/api/start` 10 秒把 ComfyUI 拉起来（日志里打的是解析后的绝对路径）。

**边界**：面板假设 ComfyUI 跑在**同一台机器**上（要直接读它的 `output\`/`input\`）。
换机器要另做一套走 ComfyUI 的 `/view` `/upload` 的版本。

## 目录与入口

```
...\project\qwen-canvas\
├── 打开画布.cmd / qwen_canvas.ps1    起面板 + 开浏览器（-NoBrowser 只起；-Stop 只停面板，**不碰 ComfyUI**）
├── qwen_setup.ps1                    建/补面板自己的 .venv（Pillow + aiohttp）
├── canvas.json                       指向哪份 ComfyUI（见上表）
├── qwen_canvas.py                    面板服务（stdlib + Pillow；aiohttp 可选）
├── qwen_canvas.html                  面板前端（单文件、无框架、无构建）
├── vendor\gsap.min.js / Flip.min.js  动效库，**本机内置、不连 CDN**（jsdelivr 直连被墙）
├── .venv\                            面板自己的 venv（不含 torch/ComfyUI）
├── canvas.log / canvas.log.err       面板日志（comfy_canvas.ps1 重定向）
└── CANVAS_NOTES.md                   本文件（面板口径）
```

- 面板 venv 要 **Pillow**（造蒙版）、**aiohttp 可选**（拿采样进度，没有就转圈）。
  本机 Python 3.13 的 `ensurepip` 坏了（安装目录缺 bundled pip wheel）⇒ `qwen_setup.ps1` 用的是
  `venv --without-pip` + 基础解释器 `--target` 装 pip，再 `-i 清华镜像` 装依赖。实测 Pillow 12.3.0 / aiohttp 3.14.3。
- ComfyUI 的起停**不归面板**：面板只在点「生成」时按 `canvas.json` 把它拉起来（分离进程），
  停止用模型目录的 `停止服务器.cmd`。面板自己用 `qwen_canvas.ps1 -Stop` 停（只杀面板进程）。
- 模型侧的权威口径（权重/显存/int8 快路径/命令行出图/局部编辑原理）在
  `..\qwen-image-2.1\QWEN21_NOTES.md`。**改面板不用读那边**，只要知道那三个模型文件名。

## 界面

新拟态（浅灰底 + 双向柔和阴影）、零原生控件：左边常驻画布，右边三个页签，底部状态栏。

**画布显示谁，按页签分**（`paintStage()` 一处管）：

| 页签 | 画布显示 |
|---|---|
| 生成 | **只显示本次会话生成出来的结果**（`madeHere`：`/api/generate` 或「整图重画」刚返回的那张）；没生成过就显示引导文案「生成页只显示本次生成的结果…」。生成请求根本不读画布上那张图（只吃提示词 / 参考图区），摆一张历史图在那儿只会让人以为"在用它生成 / 在改它" |
| 修改 / 历史 | 当前底图（`current`）—— 修改页它就是真输入 |

对比模式（`cmpState.on`）下不藏图。**注意 `showPage('gen')` 在启动时就会跑，所以 `TIP_*` 这些常量必须定义在它之前**
（放后面会 TDZ：`Cannot access 'TIP_EMPTY' before initialization`，整个页面脚本静默死掉——空网格 + 状态栏停在"就绪"就是这样来的）。

| 页签 | 内容 |
|---|---|
| **生成** | 提示词 / 尺寸（512·768·1024·1536·2048 分段胶囊）/ 步数 / 张数 / 种子（自绘步进器 − 值 +）/ 参考图区 / 生成 |
| **修改** | 整图建议 → **待改区域卡片列表** → **列表最下方才是「应用修改」**，其下是「整图按建议重画」「清除全部框」；改完顶部「改前 / 改后」按钮亮起，进画布拖中缝对比 |
| **历史** | 最近生成的**网格**（48 张以内），点一张换底图继续迭代（**留在历史页不跳转**）；**悬停格子右上角出现 ×**，点它弹自绘确认框，确认后从 `output\` 删掉该图 |

| 操作 | 效果 |
|---|---|
| 在图上**按住左键拖一个方框** | 出现"区域 N"（图上带编号徽标 + × 删除），自动跳到「修改」页并聚焦该块的建议输入框 |
| 在卡片里写一句**改动建议** | 例：`把这只玻璃壶换成白色陶瓷奶缸` |
| 点 **应用修改** | **逐块串行重画**，只改框内；每块一次采样 |
| 点 **整图按建议重画** | 不框区域，整图按"整图建议"重画（等价参考图编辑） |
| **ComfyUI 原生界面**（右上角） | 先 `POST /api/start` 再另开 8188 的官方 UI |

参考图缩略图带 **1/2/3 编号徽标**，点徽标把编号插进当前聚焦的输入框；提示词里**直接写裸数字**
（`1` = 第 1 张），服务端不做任何翻译（开关是 `qwen_canvas.html` 里的 `REF_BARE`；
裸数字与 `<image1>` 等价的实测依据在模型侧 `QWEN21_NOTES.md`）。

实测耗时（1024²/25 步，模型侧数字）：文生图 13–26 s、参考图编辑 35–40 s、应用修改每块 27–37 s。

## 参考图（「生成」页）= 编辑语义：写「要改什么」，别写描述

参考图不是风格参考 —— 它是**要改的那张图**。图被塞进 `TextEncodeQwenImage21` 的 `images.image_i`，
KSampler 从 `[4,2]`（同一节点给的空 latent，尺寸由 `resolution` 决定）起步、`cfg=1.0 / denoise=1.0`。
所以提示词写**要改成什么样**才动得起来；写"这张图是什么"的纯描述，模型没有可改的东西，就**原样吐回来**。

同一张参考图 + 同 seed（2026-09-22 实测；512² 缩略图上的 mean |a-b|，0–255；红卫衣那块是图上量的区域）：

| 提示词 | 全图 | 指令那块（红卫衣） | 其余 |
|---|---|---|---|
| `把左边第二个人的黑色T恤换成红色卫衣`（**指令**） | 5.7 | **29.1** | 3.5 |
| `1 是四个动漫角色站在欧洲街道上，动漫插画风格`（**描述**） | 3.1 | **2.1** | 3.2 |

——描述那句"指令那块"只有 2.1、全图 3.1，就是**原样返回**（只剩重渲染噪声）；指令那句只在指定区域变了 29.1，
其余 3.5。面板文案已按这条改（`#prompt` 的 placeholder、参考图那行的"不是风格参考"、拖放区那句）。

编号（缩略图徽标 1/2/3）写进提示词即可；`REF_BARE=true` 时裸数字与 `<image1>` 等价（模型侧实测）。
**多图"把 A 的元素放进 B"很弱**（两种写法都失败，见模型侧笔记）。

## 服务实现要点

- **ComfyUI HTTP API 的一层壳**，不自己加载模型：图落在 `output\`，显存是同一个服务。
- 只要 stdlib + PIL；对 127.0.0.1 的调用**显式绕过系统代理**（本机开着 7897，urllib 会把 localhost 也塞进代理）。
- **拉起 ComfyUI 用 `CREATE_NO_WINDOW`，不要 `DETACHED_PROCESS`**：venv 的 `Scripts\python.exe` 只是个 redirector
  （255 KB，基础解释器才 105 KB），它会**再 `CreateProcess` 一次**去起真解释器。`DETACHED_PROCESS`（0x8）把 console
  整个拿掉 → 孩子没有可继承的 console → Windows 给它**新分配一个** → **每次冷启都在屏幕上弹一个黑窗**
  （实测：孩子进程的窗口 `IsWindowVisible=True`，面板自己的窗口是 hidden）。换成 `CREATE_NO_WINDOW`（0x08000000）
  = 给它一个"没有窗口的 console"，孩子继承得到，屏幕干净；ComfyUI 仍然不依赖面板的 console，面板退出它照跑。
- **验收这种"有没有弹窗"必须用窗口枚举**（`EnumWindows` + `IsWindowVisible` + `GetWindowThreadProcessId`），
  别靠肉眼看任务栏、也别只查进程：修前那个窗口 `title=''`，任务栏不显眼，而进程数一点变化都没有。
- 接口：`GET /api/state`（在线状态 + `autostart` + 最近图）、`GET /api/status`（队列 + **采样步数 `step/steps`**）、
  `POST /api/start`、`POST /api/generate`、`POST /api/local`（`regions` 为空 = 整图重画）、
  `POST /api/upload`（参考图，data URL）、`POST /api/delete`（`{name}` 删 `output\` 下的某张图；
  只认裸文件名 + `.png`，`..`/子路径/非 png 一律 404）、`GET /vendor/<name>.js`、`GET /img?name=&dir=`。
- **改 `.html` 不用重启服务**（服务端每次从磁盘读）；**改 `.py` 要重启**：`qwen_canvas.ps1 -Stop` → 等 `netstat` 确认 8189
  没在 LISTEN → `打开画布.cmd -NoBrowser`（不等端口释放就起，新进程会因端口占用直接退出）。
- 前端单文件、无框架：新拟态全靠 `box-shadow` 双向阴影，控件全部自绘（`appearance:none`），
  滚动条 `::-webkit-scrollbar` 自绘，**删图确认框也是自绘的 `confirmBox()`**（不用原生 `confirm`；
  Esc/点背景 = 取消，Enter = 确定）。
- 蒙版由服务端用 PIL 按**底图原始尺寸**画（白 = 重画）；多块时**每块单独一张蒙版、单独一次采样**，
  每块出图后把它拷回 `input\` 当下一块的底图（尺寸不变，所以框选坐标继续有效）。
  模型**看不到框**，只有 conditioning 里的文字在描述要改什么 —— 面板会自动包成
  「只修改 `<image1>` 里被框选的那一块区域：`<建议>`。框外的画面必须保持原样，不要改动。」

### `input\` 中转文件的清理策略

面板每跑一次都会往 `input\` 拷底图/蒙版，原来从不清理（攒了几十个）。现在**每次 POST 前**调用
`prune_input()`（生成期间用 `input_busy()` 挡住，不动在用的底图）：

| 前缀 | 策略 | 为什么 |
|---|---|---|
| `base_` / `mask_` | 只留最新 **24** 个（`QWEN_INPUT_KEEP` 可改） | 每次局部编辑产生 2–5 个，纯中转件 |
| `ref_` | 只删超过 **7 天**的（`QWEN_INPUT_REF_TTL_HOURS` 可改） | 页面里还按文件名引用着，**按个数删会让下次生成 LoadImage 失败** |
| 其它（`example.png`、`3d\`、用户自己放进来的图） | 永不触碰 | ComfyUI 自带 / 用户的素材 |

## 动效（4 处，取自 Gasp-Design，全部本机内置）

形式抄的是用户自己的组件库 `Github/Offblink/Gasp-Design`（GSAP 3.12.7 + Flip），但**不引 CDN**：
`vendor\gsap.min.js`（72 KB）+ `vendor\Flip.min.js`（25 KB）跟着面板走，由 `GET /vendor/<name>.js` 提供
（服务端只认 `vendor\` 下的 `.js` 裸文件名）。

| 动效 | 出处手法 | 实现要点 |
|---|---|---|
| 改前/改后对比 | `before-after-slider` | `/api/local` 前记下底图名、返回后记下结果名；按钮进对比模式：叠一张「改前」在画布上，`clip-path:inset(0 X% 0 0)` 拖动分割，中缝可拖、两端 <15%/>85% 吸附；拖拽用 pointer 事件（没引 Draggable）。对比只在"结果就是当前底图 + 改前那张还在历史里"时可用 |
| 运行态进度环 + 数字递增 | `scroll-progress-ring` + `number-counter` | 状态栏的环按**真实采样步数**（`/api/status` 的 `step/steps`）填，拿不到就转圈——不编百分比；秒数用 GSAP 滚到最终值。多块串行时每块会从 0/25 重新开始 |
| 新图落盘像素化 | `pixelation-transition` | 画布上叠一层 `<canvas>`，按"马赛克块边长"26px → 1px（power2.out，0.55 s）重画当前图，最后淡出。实测屏幕上方块从 10–11 px 收到 1 px。用 canvas 2D 复刻（原组件是 three.js 的 `setPixelRatio`，不引 three.js） |
| 删卡片/删历史后补位 | `flip-drag-reorder`（GSAP Flip） | `flipWrap()`：`getState` → 重建 DOM → `Flip.from(state, {targets: 新节点})`。新卡片/新格子用 `onEnter` + stagger 入场；顺带在重建时保住区域卡片里的光标位置。宿主在隐藏页签里（`!host.offsetParent`）时**只改 DOM 不做动画**——见下面第 2 条 |

三个必须记住的坑（都踩过、都已修）：

1. **Flip 必须显式传 `targets`**。我们每次都是 `innerHTML=''` 重建 DOM，旧节点已经脱离文档；
   只写 `Flip.from(state)` 的话它动的是那些**隐形旧节点**，屏幕上的新节点纹丝不动（表现为"删卡片没有补位动画"）。
   传 `{targets: 新节点数组}` 后它才按 `data-flip-id` 跟旧位置配对。
2. **隐藏容器里不能量尺寸、更不能把量到的 0 写回 DOM**。`.page{display:none}`（切页签靠 `.on`），启动时
   `showPage('gen')` 之后「历史」页签是隐藏的，而启动流程会连着渲染两次历史（`renderHist()` → `show()` → 又一次
   `renderHist()`）。第二次渲染时 `FLIP.getState()` 量到的是隐藏元素的框：宽高全 0，Flip 就把
   `width:0;height:0;maxWidth:0;…` 当成"目标尺寸"**内联写回新格子**，inline 的 `width:0` 盖掉网格列宽 ⇒
   12 个格子全塌成 0×0（图看不见、悬停 × 也点不到）。修法是 `flipWrap` 开头一句
   `if(!FLIP || !ANIM || !host.offsetParent){ mutate(); return; }`（宿主没布局尺寸就只改 DOM 不做动画），
   外加收尾 `clearProps` 清掉 GSAP/Flip 留下的尺寸与 transform 内联值。
   **验收必须量 `getBoundingClientRect()` 宽高**：只数 `.cell` 个数、只查 `img.naturalWidth` 都查不出来
   （文件是好的、容器是 0）。同样的坑在**区域卡片**那边也成立（`mouseup` 里是 `render()` 在前、
   `showPage('edit')` 在后，所以"在生成页画新框"就是在隐藏宿主里渲染）。
3. **ComfyUI 的进度只发给"提交这个 prompt 的 client_id"**（`execution.py` 里 `server.client_id = extra_data["client_id"]`，
   广播的 `status` 事件例外）。所以进度监听 ws 和每次 `/prompt` 提交必须用**同一个 client id**（`WS_CLIENT_ID`），
   否则 `/api/status` 的 `step/steps` 永远是 0，环只会转圈。ws 地址要用 `ws://`（`http://` 起不来）。

没有 GSAP（vendor 丢了）或用户开了 `prefers-reduced-motion` 时，所有动效直接落终态，功能不受影响
（`twProxy/twEl/flipWrap` 里都有无动画分支；已实测 reduce 模式下出图/拖框/删卡片全正常）。

## 悬停 / 焦点（全站统一规则，纯 CSS）

新拟态里"悬停"就是**阴影变深 = 抬起来**、"按下"就是**阴影翻成 inset = 按进去**。全站按这一条走：

- **过渡统一挂在 `button` 基类上**（`box-shadow .18s / color .15s / background .18s / opacity .18s /
  transform .12s`），别的元素各自在基规则里加 `transition`。**不要再给单个按钮写 `transition`** ——
  `#tabs button` 原来那句 `transition:color .15s` 会把基类的其余几项盖掉（特异性更高），已经删掉。
- 凹陷槽里的按钮（页签、尺寸/张数分段）：悬停 → 从槽里"顶起来"（加一层浅 raised 阴影），选中态再深一档；
  `.on` 的按钮悬停走"变亮"分支（`#tabs button.on:not(:disabled):hover` 等），**不能让它看起来往下沉**。
- 悬浮按钮（`.bigbtn` / `.pill` / `.stepper .pm` / 确认框按钮）：悬停 → 阴影从 5–8px 加到 6–10px + 文字转 `--fg`；
  `.primary` / `.danger` 额外把渐变调亮、辉光加大。**`:hover` 规则必须写在 `:active` 之前**（同特异性，后写的赢）。
- 画布上的区域框 `.box`：`.sel` 是"正在编辑的框"（强环），`:not(.sel):hover` 只给弱一档的环 —— 悬停**不会**改变选中项
  （鼠标横穿画面去侧栏时不该抢选中；卡片那边是 `mouseenter` 选中，方向相反，故意的）。
- 历史格子 `.cell`：悬停 = 淡环 + 缩略图 `brightness(1.05)` + 文件名变亮 + 右上角 × 淡入；`×` 自己悬停再变红放大。
  **`.cell` / `.reg` 是 Flip 的 targets，悬停规则里绝不能加 `transform`**（会被 GSAP 的内联 transform 打架）。
  要"放大"只能加在没被 Flip 动的后代上（`.cell .kill`、`.ref .tag` 这些）。
- 键盘焦点：`button{outline:none}` 必须配一条 `button:focus-visible{outline:2px solid var(--accent)}`，
  否则 Tab 走一圈什么都看不见。
- 悬停**不改布局**（只动 box-shadow/color/background/filter），实测每个元素的 rect 悬停前后一致。

验收方式：CDP 真鼠标 `page.hover(sel)` 后读 `getComputedStyle` 对比，不能靠"看着像"。注意 `box-shadow` 的比对
**要拿整串**——`#drop:hover` 那类把环**追加在末尾**，截前 70 字符比会误判成"没变"。
动效验收同理：**必须量 `getBoundingClientRect()` + 截图**，只数 DOM 数量会漏掉"格子被写成 0×0"这类回归。

## 写脚本时的坑（改 `qwen_canvas.ps1` / `qwen_setup.ps1` 前必读）

1. 含中文的 `.ps1` 必须 **UTF-8 + BOM**、`.cmd` 必须**纯 ASCII + CRLF**（`.cmd` 里别放中文文件名）。
2. 从 cmd 里调这些 .cmd 时给 stdin 喂 NUL（否则 `pause` 等按键）；**从管道捕获输出时可能永远读不到 EOF**
   （有子进程继承了 stdout 写端），前台看不到返回不代表没跑成 —— 去查端口/日志。
3. 面板自己的 `.venv` 用 `qwen_setup.ps1` 建（本机 `ensurepip` 坏）；别改成 `python -m venv` 直接带 pip。
