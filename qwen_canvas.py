"""Qwen-Image-2.1 画布服务

一个跟 ComfyUI 解耦的画布面板：自己只做 UI + 组图 + 落盘，
真正出图的还是 ComfyUI（HTTP 8188），图也仍然落在它的 output\\。
两者之间只有两个接触点：**ComfyUI 的根目录**（读 output/input、要拉起时用它）
和 **HTTP 端口**。根目录按 环境变量 > 本目录 `canvas.json` > 本目录下的 ComfyUI（老布局）解析，
所以面板可以搬出模型目录单独放。

  出图：填参数（提示词/尺寸/步数/种子/张数/参考图）
  改图：在图上拖方框 + 写改动建议，只重画框住的那块
  整图改：不框，整图按建议重画（参考图编辑）

依赖：Pillow（造蒙版）；aiohttp 可选（拿采样进度，没有就转圈）。
启动:  <python> qwen_canvas.py      ->  http://127.0.0.1:8189
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CANVAS_PORT = int(os.environ.get("QWEN_CANVAS_PORT", "8189"))
COMFY_PORT = int(os.environ.get("QWEN_COMFY_PORT", "8188"))

# ---------- 面板自己的文件 ----------
ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "qwen_canvas.html"
VENDOR = ROOT / "vendor"        # 本地内置的前端库（gsap/Flip），由 /vendor/<name>.js 提供


# ---------- 面板 ↔ ComfyUI 的接缝（全文件只有这一段认 ComfyUI 的位置）----------
def _read_config() -> dict:
    """本目录下的 canvas.json（可选）。放的是 ComfyUI 在哪、用哪个 python 起它。"""
    p = ROOT / "canvas.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"canvas.json 读不动，按空配置继续: {e}", file=sys.stderr)
        return {}


CFG = _read_config()


def _cfg_path(key: str, default):
    """环境变量 QWEN_<KEY> > canvas.json 的 <key> > 默认值。相对路径按**面板目录**解析。"""
    v = os.environ.get("QWEN_" + key.upper()) or CFG.get(key)
    if not v:
        return Path(default).resolve()
    p = Path(str(v)).expanduser()
    return (p if p.is_absolute() else ROOT / p).resolve()


COMFY = _cfg_path("comfy_root", ROOT / "ComfyUI")
OUT = _cfg_path("output_dir", COMFY / "output")
INP = _cfg_path("input_dir", COMFY / "input")
PY = _cfg_path("comfy_python", COMFY / ".venv" / "Scripts" / "python.exe")
LOG = _cfg_path("comfy_log", COMFY / "qwen_server.log")
# 拉起 ComfyUI 的命令行（工作目录 = COMFY）
COMFY_CMD = [str(x) for x in (CFG.get("comfy_cmd")
                              or ["main.py", "--listen", "127.0.0.1", "--port", str(COMFY_PORT)])]
# 面板要不要自己把 ComfyUI 拉起来（false = 只当前端，服务由外部管）
COMFY_AUTOSTART = str(os.environ.get("QWEN_COMFY_AUTOSTART", CFG.get("comfy_autostart", True))).lower() \
    not in ("0", "false", "no", "")

UNET = "qwen_image_2.1_int8_convrot.safetensors"
CLIP = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"

# 本机服务之间的调用不走代理（系统里开着 7897 代理时 127.0.0.1 会被塞进去）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _api(path: str, payload: dict | None = None, timeout: float = 30.0):
    url = f"http://127.0.0.1:{COMFY_PORT}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with _OPENER.open(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body) if body else {}


def comfy_online() -> bool:
    try:
        _api("/system_stats", timeout=3)
        return True
    except Exception:
        return False


def start_comfy() -> bool:
    """把 ComfyUI 拉起来（分离进程，关掉本页面也不影响）。已经在跑就直接返回 True。"""
    if comfy_online():
        return True
    if not COMFY_AUTOSTART or not PY.exists() or not COMFY.is_dir():
        return False
    LOG.parent.mkdir(parents=True, exist_ok=True)
    # CREATE_NO_WINDOW，**不要** DETACHED_PROCESS：venv 的 Scripts\python.exe 只是个 redirector
    # （255 KB，基础解释器才 105 KB），它会再 CreateProcess 一次去起真解释器。DETACHED_PROCESS 把 console
    # 整个拿掉 → 孩子没有可继承的 console → Windows 给它新分配一个 → 每次冷启都在屏幕上弹一个黑窗。
    # 给它一个"没有窗口的 console"（CREATE_NO_WINDOW）就够：孩子继承得到，屏幕干净。
    flags = 0x08000000 | 0x00000200        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    with open(LOG, "ab") as fh:
        subprocess.Popen([str(PY), *COMFY_CMD], cwd=str(COMFY), stdin=subprocess.DEVNULL,
                         stdout=fh, stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)
    t0 = time.time()
    while time.time() - t0 < 300:
        if comfy_online():
            return True
        time.sleep(2)
    return False


# ---------- 图（跟 qwen_generate.ps1 里实测过的三套图一致）----------

def _loaders() -> dict:
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
    }


def _tail(positive_src, latent_src, steps: int, seed: int, prefix: str) -> dict:
    return {
        "5": {"class_type": "KSampler",
              "inputs": {"model": ["1", 0], "seed": int(seed), "steps": int(steps), "cfg": 1.0,
                         "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0,
                         "positive": [positive_src, 0], "negative": [positive_src, 1],
                         "latent_image": latent_src}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["3", 0]}},
        "7": {"class_type": "SaveImage", "inputs": {"images": ["6", 0], "filename_prefix": prefix}},
    }


def graph_t2i(prompt: str, size: int, steps: int, seed: int, prefix: str = "qwen21_t2i") -> dict:
    g = _loaders()
    g["4"] = {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": prompt, "negative_prompt": "",
                         "vae": ["3", 0], "resolution": int(size)}}
    g.update(_tail("4", ["4", 2], steps, seed, prefix))
    return g


def graph_edit(prompt: str, refs: list[str], size: int, steps: int, seed: int,
               prefix: str = "qwen21_edit") -> dict:
    g = _loaders()
    enc: dict = {"clip": ["2", 0], "prompt": prompt, "negative_prompt": "",
                 "vae": ["3", 0], "resolution": int(size)}
    for i, name in enumerate(refs[:16], start=1):
        nid = 20 + i
        g[str(nid)] = {"class_type": "LoadImage", "inputs": {"image": name}}
        enc[f"images.image_{i}"] = [str(nid), 0]
    g["4"] = {"class_type": "TextEncodeQwenImage21", "inputs": enc}
    g.update(_tail("4", ["4", 2], steps, seed, prefix))
    return g


def graph_local(prompt: str, base: str, mask: str, size: int, steps: int, seed: int,
                prefix: str = "qwen21_local") -> dict:
    """局部编辑：采样起点 = 底图自己的 latent，只有蒙版（白=要改）区域会被重画。"""
    g = _loaders()
    g["21"] = {"class_type": "LoadImage", "inputs": {"image": base}}
    g["30"] = {"class_type": "LoadImageMask", "inputs": {"image": mask, "channel": "red"}}
    g["31"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["21", 0], "vae": ["3", 0]}}
    g["32"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["31", 0], "mask": ["30", 0]}}
    g["4"] = {"class_type": "TextEncodeQwenImage21",
              "inputs": {"clip": ["2", 0], "prompt": prompt, "negative_prompt": "",
                         "vae": ["3", 0], "resolution": int(size), "images.image_1": ["21", 0]}}
    g.update(_tail("4", ["32", 0], steps, seed, prefix))
    return g


def run_graph(graph: dict, timeout: float = 1800.0) -> tuple[list[str], float]:
    """提交并等到出图。返回（output 里的文件名列表, 秒数）。"""
    cid = WS_CLIENT_ID          # 必须和进度监听用同一个 id，否则收不到 progress
    try:
        resp = _api("/prompt", {"prompt": graph, "client_id": cid}, timeout=120)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"提交失败 {e.code}: {e.read().decode('utf-8', 'replace')[:800]}") from None
    qid = resp["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1.0)
        try:
            hist = _api(f"/history/{qid}", timeout=20)
        except Exception:
            continue
        entry = hist.get(qid)
        if not entry:
            continue
        st = entry.get("status", {})
        if st.get("status_str") == "error":
            msgs = [str(m) for m in st.get("messages", [])]
            raise RuntimeError("生成失败: " + " | ".join(msgs)[-800:])
        files = [img["filename"]
                 for node_out in entry.get("outputs", {}).values()
                 for img in node_out.get("images", [])]
        if files:
            return files, time.time() - t0
    raise RuntimeError("等待超时")


# ---------- 文件小工具 ----------

def safe_name(name: str) -> str:
    base = os.path.basename(str(name).replace("\\", "/"))
    if not base or base != str(name).replace("\\", "/").split("/")[-1]:
        base = os.path.basename(str(name))
    if ".." in base or base.strip() == "":
        raise ValueError("非法文件名")
    return base


def stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time()*1000) % 1000:03d}"


def copy_into_input(src: Path, prefix: str) -> str:
    INP.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{stamp()}{src.suffix or '.png'}"
    shutil.copy2(src, INP / name)
    return name


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def make_mask(base_image: Path, rects: list[dict], out_name: str) -> str:
    """按底图尺寸画一张黑白蒙版（白=要重画的地方）。"""
    from PIL import Image, ImageDraw
    w, h = image_size(base_image)
    m = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(m)
    for r in rects:
        x1 = max(0, min(int(r["x1"]), w - 1))
        y1 = max(0, min(int(r["y1"]), h - 1))
        x2 = max(x1 + 1, min(int(r["x2"]), w))
        y2 = max(y1 + 1, min(int(r["y2"]), h))
        d.rectangle([x1, y1, x2 - 1, y2 - 1], fill=(255, 255, 255))
    INP.mkdir(parents=True, exist_ok=True)
    m.save(INP / out_name)
    return out_name


def save_data_url(data_url: str, prefix: str) -> str:
    """把浏览器上传的 data URL 存进 input\\，返回文件名。"""
    import base64
    m = re.match(r"data:image/(png|jpeg|jpg|webp|bmp);base64,(.*)$", data_url, re.S | re.I)
    if not m:
        raise ValueError("只认 PNG/JPEG/WebP/BMP 的图片数据")
    ext = {"jpeg": ".jpg", "jpg": ".jpg"}.get(m.group(1).lower(), "." + m.group(1).lower())
    raw = base64.b64decode(m.group(2))
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("图片太大了（上限 32 MB）")
    INP.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{stamp()}{ext}"
    (INP / name).write_bytes(raw)
    return name


# ---------- input\ 卫生 ----------
# 画布每跑一次都会往 input\ 拷底图/蒙版（base_/mask_），从不清理，攒多了很乱。
# 策略分两类：
#   base_/mask_  = 每次采样的中转件，只留最新 INPUT_KEEP 个（一次局部编辑产生 2–5 个）；
#   ref_         = 用户上传的参考图，页面里还按文件名引用着，**不能按个数删**
#                  （删了下次点生成就会 LoadImage 失败），只删掉超过 INPUT_REF_TTL 小时的。
# ComfyUI 自带的 example.png / 3d\、以及用户自己放进来的其它图一律不动。
# 生成中途不清理（in-flight 那一块的底图还要用）。
_INPUT_TMP_PREFIXES = ("base_", "mask_")
_INPUT_REF_PREFIX = "ref_"
_INPUT_PROTECTED = {"example.png"}
INPUT_KEEP = int(os.environ.get("QWEN_INPUT_KEEP", "24"))
INPUT_REF_TTL = int(os.environ.get("QWEN_INPUT_REF_TTL_HOURS", str(24 * 7))) * 3600
_input_busy = 0


class input_busy:
    """with input_busy(): … —— 期间 prune_input() 不动手。"""

    def __enter__(self):
        global _input_busy
        _input_busy += 1

    def __exit__(self, *exc):
        global _input_busy
        _input_busy -= 1


def prune_input(keep: int | None = None) -> int:
    """清掉画布自己拷进来的中转文件。返回删了几个。"""
    if _input_busy:
        return 0
    keep = INPUT_KEEP if keep is None else keep
    if not INP.exists():
        return 0
    now = time.time()
    tmp, old_refs = [], []
    for p in INP.iterdir():
        if not p.is_file() or p.name in _INPUT_PROTECTED:
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if p.name.startswith(_INPUT_TMP_PREFIXES):
            tmp.append((mt, p))
        elif p.name.startswith(_INPUT_REF_PREFIX) and now - mt > INPUT_REF_TTL:
            old_refs.append((mt, p))
    tmp.sort(key=lambda t: t[0], reverse=True)
    gone = 0
    for _, p in tmp[max(keep, 0):] + old_refs:
        try:
            p.unlink()
            gone += 1
        except OSError:
            pass
    return gone



# ---------- 采样进度（给状态栏的进度环用）----------
# ComfyUI 每采样一步就通过自己的 WebSocket 广播 {"type":"progress","data":{value,max,...}}。
# 我们只是把它抄进内存，供 /api/status 读——拿不到（没装 aiohttp / ComfyUI 没起）时
# 前端退回"不确定进度"的转圈，**不编造百分比**。单次采样一趟 25 步，多块串行时每块会重新从 0 开始。
_PROGRESS = {"step": 0, "steps": 0, "active": False}
# ComfyUI 把 progress/executing/executed 只发给"提交这个 prompt 的 client_id"对应的那个 ws 连接
# （execution.py 里 `server.client_id = extra_data["client_id"]`），所以监听和提交必须用同一个 id。
WS_CLIENT_ID = uuid.uuid4().hex


def _start_progress_listener() -> bool:
    try:
        import aiohttp  # noqa: F401
    except Exception:
        return False
    threading.Thread(target=_progress_loop, daemon=True, name="comfy-progress").start()
    return True


def _progress_loop() -> None:
    import asyncio

    import aiohttp

    async def run() -> None:
        url = f"ws://127.0.0.1:{COMFY_PORT}/ws?clientId={WS_CLIENT_ID}"
        complained = False
        while True:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.ws_connect(url, heartbeat=30, max_msg_size=0) as ws:
                        complained = False
                        async for msg in ws:
                            if msg.type is not aiohttp.WSMsgType.TEXT:
                                continue
                            try:
                                d = json.loads(msg.data)
                            except Exception:  # noqa: BLE001
                                continue
                            t, data = d.get("type"), d.get("data") or {}
                            if t == "progress":
                                _PROGRESS.update(active=True, step=int(data.get("value") or 0),
                                                 steps=int(data.get("max") or 0))
                            elif t == "executing":
                                if data.get("node") is None:      # 整个 prompt 跑完
                                    _PROGRESS.update(active=False, step=0, steps=0)
                            elif t in ("execution_error", "execution_interrupted"):
                                _PROGRESS.update(active=False, step=0, steps=0)
            except Exception as e:  # noqa: BLE001   ComfyUI 没起/重启中：静默重试
                _PROGRESS.update(active=False, step=0, steps=0)
                if not complained:      # 只在连续失败的第一轮说一次，别刷屏
                    complained = True
                    print(f"[progress] 连不上 ComfyUI 的 /ws：{type(e).__name__}: {e}", file=sys.stderr,
                          flush=True)
            await asyncio.sleep(3)

    asyncio.run(run())


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "QwenCanvas/1.0"

    def log_message(self, fmt, *args):  # 别把每个请求都刷到控制台
        pass

    # --- 输出助手 ---
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": str(msg)}, code)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def _file(self, path: Path, ctype: str, cache: str = "no-store"):
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    # --- 路由 ---
    def do_GET(self):
        try:
            if self.path in ("/", "/index.html"):
                self._file(PAGE, "text/html; charset=utf-8")
            elif self.path == "/favicon.ico":
                self.send_response(204); self.end_headers()
            elif self.path.startswith("/api/state"):
                self._json({"ok": True, "comfy_online": comfy_online(), "comfy_port": COMFY_PORT,
                            "canvas_port": CANVAS_PORT, "autostart": COMFY_AUTOSTART,
                            "recent": sorted((p.name for p in OUT.glob("*.png")),
                                             key=lambda n: (OUT / n).stat().st_mtime,
                                             reverse=True)[:24]})
            elif self.path.startswith("/api/status"):
                info = {"ok": True, "running": False, "queue": 0,
                        "step": _PROGRESS["step"], "steps": _PROGRESS["steps"],
                        "sampling": _PROGRESS["active"]}
                try:
                    q = _api("/queue", timeout=5)
                    info["running"] = len(q.get("queue_running", [])) > 0
                    info["queue"] = len(q.get("queue_pending", []))
                except Exception:
                    pass
                self._json(info)
            elif self.path.startswith("/vendor/"):
                # 本地内置的前端库（gsap/Flip）。只认 vendor\ 下的 .js 裸文件名。
                name = safe_name(self.path[len("/vendor/"):])
                p = VENDOR / name
                if not name.endswith(".js") or p.parent != VENDOR or not p.is_file():
                    return self._err("没有这个文件", 404)
                return self._file(p, "text/javascript; charset=utf-8", cache="max-age=86400")
            elif self.path.startswith("/img"):
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                name = safe_name(qs.get("name", [""])[0])
                d = qs.get("dir", ["output"])[0]
                base = OUT if d != "input" else INP
                p = base / name
                if not p.exists():
                    return self._err("找不到图片", 404)
                ctype = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
                self._file(p, ctype)
            else:
                self._err("未知路径", 404)
        except Exception as e:  # noqa: BLE001
            self._err(e, 500)

    def do_POST(self):
        try:
            body = self._body()
            prune_input()
            if self.path == "/api/start":
                ok = start_comfy()
                return self._json({"ok": ok, "comfy_online": comfy_online()})

            if self.path == "/api/upload":
                name = save_data_url(body.get("data", ""), "ref")
                return self._json({"ok": True, "name": name})

            if self.path == "/api/generate":
                if not comfy_online() and not start_comfy():
                    return self._err("ComfyUI 服务没起来", 503)
                prompt = str(body.get("prompt") or "").strip()
                if not prompt:
                    return self._err("提示词不能为空")
                size = int(body.get("size") or 1024)
                steps = int(body.get("steps") or 25)
                count = max(1, min(int(body.get("count") or 1), 8))
                seed = int(body.get("seed", -1))
                refs = [safe_name(r) for r in (body.get("refs") or [])]
                files, total = [], 0.0
                for i in range(count):
                    s = (seed + i) if seed >= 0 else (int.from_bytes(os.urandom(4), "little") % 2147483647)
                    g = (graph_edit(prompt, refs, size, steps, s) if refs
                         else graph_t2i(prompt, size, steps, s))
                    out, sec = run_graph(g)
                    files += out
                    total += sec
                return self._json({"ok": True, "images": files, "seconds": round(total, 1)})

            if self.path == "/api/delete":
                # 历史里删掉某张 output 图。只认 output\ 下的 .png 裸文件名。
                raw = str(body.get("name") or "").strip()
                if not raw:
                    return self._err("缺少文件名")
                try:
                    name = safe_name(raw)
                except ValueError as e:
                    return self._err(e)
                p = OUT / name
                if p.suffix.lower() != ".png" or p.parent != OUT or not p.is_file():
                    return self._err("没有这张图", 404)
                p.unlink()
                return self._json({"ok": True, "name": name})

            if self.path == "/api/local":
                if not comfy_online() and not start_comfy():
                    return self._err("ComfyUI 服务没起来", 503)
                base_name = safe_name(body.get("image") or "")
                if not base_name:
                    return self._err("缺少底图")
                size = int(body.get("size") or 1024)
                steps = int(body.get("steps") or 25)
                seed = int(body.get("seed", -1))
                regions = body.get("regions") or []
                whole = str(body.get("prompt") or "").strip()
                if not regions and not whole:
                    return self._err("要框一块区域，或者写一条整图建议")

                base = OUT / base_name
                if not base.exists():
                    return self._err("底图不存在", 404)
                passes, total = [], 0.0
                with input_busy():
                    base_ref = copy_into_input(base, "base")
                    for idx, r in enumerate(regions, start=1):
                        note = str(r.get("note") or "").strip()
                        if not note:
                            return self._err(f"第 {idx} 块还没写改动建议")
                        mask = make_mask(base, [r], f"mask_{stamp()}.png")
                        prompt = (f"只修改<image1>里被框选的那一块区域：{note}。"
                                  f"框外的画面必须保持原样，不要改动。")
                        s = (seed + idx - 1) if seed >= 0 else (int.from_bytes(os.urandom(4), "little") % 2147483647)
                        g = graph_local(prompt, base_ref, mask, size, steps, s)
                        out, sec = run_graph(g)
                        total += sec
                        passes.append({"region": idx, "note": note, "file": out[0], "seconds": round(sec, 1)})
                        # 下一块的底图 = 这一块的结果（尺寸不变，框选坐标继续有效）
                        base_ref = copy_into_input(OUT / out[0], "base")
                if not regions:  # 整图按建议重画 = 不带蒙版的参考图编辑
                    refs = [base_ref] + [safe_name(x) for x in (body.get("extra_refs") or [])]
                    g = graph_edit(whole, refs, size, steps,
                                   seed if seed >= 0 else int.from_bytes(os.urandom(4), "little") % 2147483647)
                    out, sec = run_graph(g)
                    total += sec
                    passes.append({"region": 0, "note": whole, "file": out[0], "seconds": round(sec, 1)})
                return self._json({"ok": True, "image": passes[-1]["file"], "passes": passes,
                                   "seconds": round(total, 1)})

            self._err("未知路径", 404)
        except Exception as e:  # noqa: BLE001
            self._err(e, 500)


def main() -> int:
    if not COMFY.is_dir():
        # 面板不再因为"找不到 ComfyUI"就拒绝启动：起得来，状态栏才能告诉你它没在线
        print(f"提示: 没找到 ComfyUI 目录 {COMFY}（面板照常起，出图会报错）", file=sys.stderr)
    srv = ThreadingHTTPServer(("127.0.0.1", CANVAS_PORT), Handler)
    srv.daemon_threads = True
    gone = prune_input()
    listening = _start_progress_listener()
    print(f"Qwen 画布: http://127.0.0.1:{CANVAS_PORT}   (ComfyUI {COMFY_PORT} @ {COMFY})" +
          ("" if COMFY_AUTOSTART else "   [不自动起 ComfyUI]") +
          (f"   清掉 {gone} 个 input 中转文件" if gone else "") +
          ("" if listening else "   [采样进度不可用：没有 aiohttp]"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
