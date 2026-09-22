# Qwen 画布（Qwen-canvas）

本地出图模型的一个**画布面板**：填参数出图 → 在图上拖个方框写一句改动建议 → **只重画框里那一块** →
点历史图继续迭代。单文件前端、零构建、零 CDN。**它自己不跑模型**：出图的还是 ComfyUI，
面板跟它只有两个接触点 —— **ComfyUI 的根目录**（读 `output\`、写 `input\`）和**它的端口** ——
所以面板能单独放、单独搬、指向任何一份同机的 ComfyUI。

一个可选的**提示词优化**开关（右上角）：打开后提交前先让一个大模型把口语化的要求改写成图像模型吃得动的指令
（「头都露出来了」→「让头部完全收进头盔里面、面罩闭合，头盔与领口自然衔接」，并补上"不能丢"的东西），
不懂"该怎么写指令"也能用。没配大模型 API key 时这个开关直接禁用，出图链路一点不受影响。

> A neumorphic canvas WebUI for a local ComfyUI model: text-to-image, *drag a box + write an instruction → only that
> region is repainted*, history grid. Single-file frontend, no build, no CDN.
> Optional LLM prompt-rewrite switch; the history grid is drag-reorderable.

![生成页](docs/panel-generate.png)

![框选 + 改动建议 → 只重画框内](docs/panel-edit.png)

## 快速开始（Windows）

前提：一份能出图的 ComfyUI（自带 `.venv` 与 `main.py`）+ Python 3.12 / 3.13。面板**不挑模型**；本仓的实测数字是在
[Qwen-Image-2.1](https://github.com/QwenLM/Qwen-Image-2.1) 上跑的，ComfyUI 直接可用的权重在
[`Comfy-Org/Qwen-Image-2.1`](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)
（[ModelScope 镜像](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1) 本机实测快得多）。

```bat
git clone https://github.com/Offblink/Qwen-canvas && cd Qwen-canvas
copy canvas.json.example canvas.json                                  REM 改成你的 ComfyUI 路径
powershell -NoProfile -ExecutionPolicy Bypass -File qwen_setup.ps1    REM 面板自己的 venv（Pillow，约 35 MB）
打开画布.cmd                                                          REM → http://127.0.0.1:8189
```

停面板：`powershell -File qwen_canvas.ps1 -Stop`（只杀面板，**不碰 ComfyUI**）。点「生成」时面板按 `canvas.json`
把 ComfyUI 拉起来（`comfy_autostart: false` 就只当前端），**停 ComfyUI 归模型侧自己的脚本**。
入口脚本是 PowerShell（Windows）；服务端是纯 Python，`python qwen_canvas.py` 在别的系统上一样跑。

## 配置

优先级 **环境变量 `QWEN_<KEY>` > 同目录 `canvas.json` > 默认值**；相对路径按面板目录解析。

| 键 | 默认 | 作用 |
|---|---|---|
| `comfy_root` | `<面板>\ComfyUI` | ComfyUI 根目录 |
| `output_dir` / `input_dir` | `<comfy_root>\output` / `\input` | 历史图来源 / 中转件落点 |
| `comfy_python` · `comfy_cmd` · `comfy_log` | `<comfy_root>\.venv\Scripts\python.exe` · `main.py --listen …` · `qwen_server.log` | 怎么把 ComfyUI 拉起来 |
| `comfy_autostart` | `true` | `false` = 面板绝不自己拉 ComfyUI |
| `llm`（可选） | 不配就没有优化开关 | 提示词优化用的大模型：`base_url` / `model` / `api_key_env` / `proxy` / `vision` / `timeout`。**API key 只从环境变量读**（默认 `DEEPSEEK_API_KEY`），别写进 `canvas.json` |
| `QWEN_CANVAS_PORT` / `QWEN_COMFY_PORT`（环境变量） | `8189` / `8188` | 面板 / ComfyUI 端口 |

完整表（含 `QWEN_INPUT_KEEP`、`QWEN_INPUT_REF_TTL_HOURS`、`QWEN_LLM_*`）与 HTTP 接口清单都在 `CANVAS_NOTES.md`。

## 怎么用

新拟态、零原生控件：左边画布，右边三个页签（生成 / 修改 / 历史），底部状态栏。
画布显示谁按页签分 —— **生成页只显示本次生成的结果，修改 / 历史页显示当前底图**。

- **生成**：提示词 / 尺寸 / 步数 / 张数 / 种子（都是自绘控件）/ 参考图。点生成**不动画布**；出图后清空提示词，
  参考图与各参数留着（失败不清，方便改完重试）。**等图的时候面板不锁**：页签照切、参数照调（下一轮用）、
  历史照翻，只有两页底部的动作按钮灰着（免得重复提交）。
- **参考图 = 「要改的那张图」**，不是风格参考：编号写进提示词即可（缩略图上的 1/2/3 就是提示词里的 `1` `2` `3`）。
  要写**「把 1 里的…换成…」**这种改动指令 —— **写纯描述会原样返回**（同图同 seed 实测：指令句只在指定区域差
  29.1/255，描述句那块只差 2.1）。多图「把 A 的元素放进 B」很弱。
- **修改**：在图上拖方框 → 卡片里写一句建议 → 点列表最下面的**应用修改**（逐块串行重画，只改框内，模型看不到框）；
  或「整图按建议重画」。改完顶部「改前 / 改后」可拖动对比。
- **提示词优化**（右上角开关，可选）：打开后**每次提交前**先让大模型改一遍 —— 生成页的提示词会被写得更具体，
  修改页的建议会被翻成「要改成什么样」的指令（抱怨句、半截话都能用），改写结果当场写回输入框 / 卡片，便于核对。
  没配 API key 时开关禁用。
- **历史**：最近 48 张的网格（**固定三列**），点一张换底图继续迭代（不跳页）；**按住格子拖动可换顺序**，
  拖到哪儿其它格子就**滑过去补位**（260 ms FLIP，半路改主意也不会跳），顺序存服务端、重启/换浏览器都在
  （新出的图仍在最前面）；悬停格子右上角 `×` 删图（自绘确认框）。

## 实测

2026-09-21/22，RTX 5070 Ti Laptop 12 GB + Qwen-Image-2.1，1024²/25 步：
文生图 13–26 s（768²/8 步 7.3 s）· 参考图编辑 27–40 s · 局部重画每块 27–37 s（768²/8 步 9.1 s）·
冷启 ComfyUI ~10 s 且**不弹控制台窗口** · 进度环按 `/api/status` 的真实采样步数填 ·
面板 venv 35 MB（Pillow 12.3.0 + aiohttp 3.14.3，不含 torch）。

提示词优化（`deepseek-v4-flash-vision-exp`，带整图 + 框选区域两张图）一次往返 **0.9–3.7 s**。
同一张底图、同一个种子、同一块框选（1024²/25 步）的对照：原话「头都露出来了」直接提交 → 模型把头整个拿掉、
只剩一个空领口环；优化后 → 头完整收在透明头盔里，宇航服、胸前装置、月球背景与构图不变。

## 边界

- 只支持**同机** ComfyUI（要直接读写它的 `output\` / `input\`）；跨机器得另做走 `/view` `/upload` 的版本。
- 单用户本机工具：**无鉴权、无并发保护**，`/api/delete` 能删 `output\` 里的图 —— **别往公网暴露**。
- 起停包装是 PowerShell（Windows）；服务端本身跨平台。

## 目录

```
打开画布.cmd / qwen_canvas.ps1   起面板 + 开浏览器（-NoBrowser 只起；-Stop 只停面板）
qwen_setup.ps1                   建/补面板自己的 .venv（Pillow + aiohttp）
canvas.json.example              配置样例（拷成 canvas.json）
qwen_canvas.py / qwen_canvas.html  面板服务（stdlib + Pillow）/ 单文件前端
vendor/                          gsap.min.js + Flip.min.js（本机内置，不连 CDN）
docs/                            README 截图
CANVAS_NOTES.md                  面板口径：接口、动效与悬停规则、参考图语义、中转清理、踩过的坑（改动前先读）
```

## 相关项目

| | |
|---|---|
| **Qwen-Image-2.1**（实测用的模型） | [QwenLM/Qwen-Image-2.1](https://github.com/QwenLM/Qwen-Image-2.1) · [HF `Qwen/Qwen-Image-2.1`](https://huggingface.co/Qwen/Qwen-Image-2.1)；ComfyUI 单文件权重 [HF `Comfy-Org/Qwen-Image-2.1`](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) / [ModelScope 镜像](https://modelscope.cn/models/Comfy-Org/Qwen-Image-2.1) |
| **ComfyUI**（后端，出图与显存都在它那） | [Comfy-Org/ComfyUI](https://github.com/Comfy-Org/ComfyUI)（GPL-3.0） |
| **Gasp-Design**（动效与交互手法的出处） | [Offblink/Gasp-Design](https://github.com/Offblink/Gasp-Design)：改前/改后分割、进度环 + 数字递增、像素化落盘、Flip 补位，四个手法都取自它现成的组件 |

本仓只含面板本身（MIT，见 `LICENSE`）。模型权重与 ComfyUI 各有各的授权（Qwen-Image-2.1 是 Qwen Research
License，非宽松，商用前先确认），本仓不再分发它们。
