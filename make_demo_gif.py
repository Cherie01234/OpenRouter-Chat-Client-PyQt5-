"""Generate a demo GIF of the OpenRouter chat client (Pillow mockup).

    python make_demo_gif.py  ->  images/demo.gif (+ images/_preview.png)

配置・配色は実機のスクリーンショットから採寸してある（1062px 幅、
ボタン幅 141 / 間隔 7、背景 #2d2d2d、会話エリア #1e1e1e、ボタン #2B5B84）。
UI を変えたときはこのファイルの LAYOUT を直すこと。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent

# ── 配色（GUI.py のスタイル定数に対応） ──────────────────────────
W                = 1062
BG               = (45, 45, 45)      # QPalette.Window
PANEL            = (30, 30, 30)      # TEXT_AREA_STYLE の背景
PANEL_BORDER     = (58, 58, 58)
FRAME_BORDER     = (70, 70, 70)
INPUT_BG         = (255, 255, 255)
INPUT_BORDER     = (204, 204, 204)
INPUT_TEXT       = (0, 0, 0)
PLACEHOLDER      = (130, 130, 130)
BTN              = (43, 91, 132)     # #2B5B84
BTN_BORDER       = (30, 65, 93)      # #1E415D
BTN_PRESSED      = (30, 65, 93)
BTN_CHECKED      = (155, 38, 38)     # #9B2626
BTN_DISABLED     = (85, 85, 85)      # #555555
BTN_DISABLED_TXT = (136, 136, 136)   # #888888
TEXT             = (232, 232, 232)
LABEL            = (225, 225, 225)
MUTED            = (154, 154, 154)   # usage_label
TITLEBAR         = (243, 243, 243)
TITLEBAR_TEXT    = (32, 32, 32)
USER             = (130, 184, 232)   # #82b8e8
ASSIST           = (126, 200, 160)   # #7ec8a0
CARET            = (232, 232, 232)

JP_FONTS = ["C:/Windows/Fonts/YuGothR.ttc", "C:/Windows/Fonts/meiryo.ttc",
            "C:/Windows/Fonts/msgothic.ttc", "C:/Windows/Fonts/arial.ttf"]


def font(size):
    for path in JP_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE, F_BODY, F_UI, F_SMALL = font(13), font(14), font(12), font(11)

# ── レイアウト（実機採寸ベース） ──────────────────────────────
# システムプロンプト欄は入力欄と左右を揃える（GUI.py の _align_input_widths）
LAYOUT = {
    "titlebar":   (0, 0, W, 30),
    "conv":       (6, 37, W - 6, 237),
    "reasoning":  (6, 243, W - 6, 303),
    "sp_toggle":  (16, 311, W - 16, 333),
    "sp_preset":  (16, 336, W - 16, 360),
    "sp_input":   (16, 363, W - 16, 415),
    "frame":      (6, 423, W - 6, 605),
    "msg_input":  (16, 431, W - 16, 493),
    "img_row":    (16, 499, W - 16, 521),
    "set_row":    (16, 527, W - 16, 551),
    "usage":      (16, 557, W - 16, 569),
    "btn_row":    (16, 575, W - 16, 597),
    "status":     (0, 609, W, 633),
}
H = 634

BUTTONS = ["送信", "再生成", "キャンセル", "クリア",
           "保存(JSON)", "保存(MD)", "読み込み", "編集モード"]

# ── 台本 ────────────────────────────────────────────────
USER_MSG = "ストリーミング表示の利点を、3点にまとめて教えてください。"
ASSIST_MSG = (
    "ストリーミング表示には、主に次の3つの利点があります。\n"
    "1. 最初のトークンが届いた時点で読み始められるため、体感の待ち時間が短い\n"
    "2. 途中で方向性の誤りに気づけるので、最後まで待たずに中断できる\n"
    "3. 応答が長い場合でも、処理が進んでいることが目に見える"
)
REASONING = ("【思考レベル: high】\n"
             "利点を「体感速度」「中断可能性」「進捗の可視化」の3軸に整理して提示する。")
SYSTEM_PROMPT = "回答は簡潔に、要点を箇条書きでまとめてください。"
PRESET_NAME = "簡潔にまとめる"
FILE_NAME = "conversation_20260802.json"

USAGE_IDLE = "履歴: 約 3,120 tok / 1,048,576 (0.3%) ｜ 次回入力: 約 $0.00134"
USAGE_DONE = ("履歴: 約 3,486 tok / 1,048,576 (0.3%) ｜ 次回入力: 約 $0.00150"
              " ｜ セッション累計: $0.00612")


# ── 描画部品 ────────────────────────────────────────────
def box(d, rect, fill, outline=None):
    d.rectangle([rect[0], rect[1], rect[2], rect[3]], fill=fill, outline=outline)


def button(d, x, y, w, h, text, *, state="normal"):
    fill, fg = {
        "normal":   (BTN, "white"),
        "pressed":  (BTN_PRESSED, "white"),
        "checked":  (BTN_CHECKED, "white"),
        "disabled": (BTN_DISABLED, BTN_DISABLED_TXT),
    }[state]
    d.rounded_rectangle([x, y, x + w, y + h], 3, fill=fill,
                        outline=BTN_BORDER if state != "disabled" else None)
    tw = d.textlength(text, font=F_UI)
    d.text((x + (w - tw) / 2, y + (h - 15) / 2), text, font=F_UI, fill=fg)


def field(d, x, y, w, h, text, *, muted=False, arrow=False, spin=False):
    """コンボ・スピン・入力欄。白背景に黒文字。"""
    d.rectangle([x, y, x + w, y + h], fill=INPUT_BG, outline=INPUT_BORDER)
    d.text((x + 5, y + (h - 15) / 2), text, font=F_UI,
           fill=PLACEHOLDER if muted else INPUT_TEXT)
    if arrow:
        cx, cy = x + w - 11, y + h / 2
        d.polygon([(cx - 4, cy - 2), (cx + 4, cy - 2), (cx, cy + 3)], fill=(70, 70, 70))
        d.line([x + w - 22, y + 1, x + w - 22, y + h - 1], fill=(225, 225, 225))
    if spin:
        cx = x + w - 9
        d.polygon([(cx - 4, y + 8), (cx + 4, y + 8), (cx, y + 3)], fill=(70, 70, 70))
        d.polygon([(cx - 4, y + h - 8), (cx + 4, y + h - 8), (cx, y + h - 3)],
                  fill=(70, 70, 70))
        d.line([x + w - 19, y + 1, x + w - 19, y + h - 1], fill=(225, 225, 225))


def wrap(d, text, fnt, max_w):
    """日本語向けに1文字ずつ折り返す。改行はそのまま尊重する。"""
    lines = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if d.textlength(cur + ch, font=fnt) <= max_w:
                cur += ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def draw_titlebar(d, dirty: bool):
    x0, y0, x1, y1 = LAYOUT["titlebar"]
    box(d, (x0, y0, x1, y1), TITLEBAR)
    d.rectangle([10, 9, 22, 21], fill=(60, 100, 150))
    mark = "*" if dirty else ""
    d.text((30, 8), f"OpenRouter Chat - PyQt5 ｜ {mark}{FILE_NAME}",
           font=F_TITLE, fill=TITLEBAR_TEXT)
    for i, glyph in enumerate(("－", "□", "✕")):
        d.text((W - 108 + i * 36, 8), glyph, font=F_TITLE, fill=TITLEBAR_TEXT)


def draw_conversation(d, shown: str, streaming: bool):
    x0, y0, x1, y1 = LAYOUT["conv"]
    box(d, (x0, y0, x1, y1), PANEL, PANEL_BORDER)

    x, y = x0 + 12, y0 + 10
    d.text((x, y), "あなた:", font=F_BODY, fill=USER)
    offset = d.textlength("あなた: ", font=F_BODY)
    d.text((x + offset, y), USER_MSG, font=F_BODY, fill=TEXT)
    y += 30

    if shown is None:
        return
    d.text((x, y), "DeepSeek:", font=F_BODY, fill=ASSIST)
    y += 24
    for line in wrap(d, shown, F_BODY, x1 - x0 - 30):
        d.text((x, y), line, font=F_BODY, fill=TEXT)
        y += 22
    if streaming:
        last = wrap(d, shown, F_BODY, x1 - x0 - 30)[-1]
        d.text((x + d.textlength(last, font=F_BODY), y - 22), "▌",
               font=F_BODY, fill=CARET)


def draw_reasoning(d, visible: bool):
    x0, y0, x1, y1 = LAYOUT["reasoning"]
    box(d, (x0, y0, x1, y1), PANEL, PANEL_BORDER)
    if not visible:
        return
    y = y0 + 8
    for line in wrap(d, REASONING, F_UI, x1 - x0 - 24):
        d.text((x0 + 12, y), line, font=F_UI, fill=(178, 178, 178))
        y += 19


def draw_system_prompt(d):
    x0, y0, x1, y1 = LAYOUT["sp_toggle"]
    button(d, x0, y0, x1 - x0, y1 - y0, "▼ システムプロンプト", state="checked")

    px0, py0, px1, py1 = LAYOUT["sp_preset"]
    d.text((px0, py0 + 5), "プリセット:", font=F_UI, fill=LABEL)
    field(d, px0 + 70, py0, 190, py1 - py0, PRESET_NAME, arrow=True)
    button(d, px0 + 268, py0 + 1, 52, 22, "保存")
    button(d, px0 + 326, py0 + 1, 52, 22, "削除")

    sx0, sy0, sx1, sy1 = LAYOUT["sp_input"]
    d.rectangle([sx0, sy0, sx1, sy1], fill=INPUT_BG, outline=INPUT_BORDER)
    d.text((sx0 + 6, sy0 + 6), SYSTEM_PROMPT, font=F_UI, fill=INPUT_TEXT)


def draw_input_area(d, stage: str):
    fx0, fy0, fx1, fy1 = LAYOUT["frame"]
    d.rectangle([fx0, fy0, fx1, fy1], outline=FRAME_BORDER)

    mx0, my0, mx1, my1 = LAYOUT["msg_input"]
    d.rectangle([mx0, my0, mx1, my1], fill=INPUT_BG, outline=INPUT_BORDER)
    if stage in ("idle", "press"):
        d.text((mx0 + 6, my0 + 6), USER_MSG, font=F_BODY, fill=INPUT_TEXT)
    else:
        d.text((mx0 + 6, my0 + 6), "メッセージを入力 … (Ctrl+Enter で送信)",
               font=F_BODY, fill=PLACEHOLDER)

    ix0, iy0, ix1, iy1 = LAYOUT["img_row"]
    button(d, ix0, iy0, 92, iy1 - iy0, "画像を追加", state="disabled")
    button(d, ix0 + 98, iy0, 96, iy1 - iy0, "画像をクリア")
    d.text((ix0 + 202, iy0 + 4), "選択された画像: なし", font=F_UI, fill=LABEL)

    sx0, sy0, sx1, sy1 = LAYOUT["set_row"]
    h = sy1 - sy0
    d.text((sx0, sy0 + 5), "モデル:", font=F_UI, fill=LABEL)
    field(d, sx0 + 50, sy0, 210, h, "deepseek/deepseek-v4-pro", arrow=True)
    d.text((sx0 + 272, sy0 + 5), "思考レベル:", font=F_UI, fill=LABEL)
    field(d, sx0 + 348, sy0, 90, h, "high", arrow=True)
    d.rectangle([sx0 + 452, sy0 + 5, sx0 + 465, sy0 + 18], fill=INPUT_BG,
                outline=INPUT_BORDER)
    d.line([sx0 + 455, sy0 + 11, sx0 + 458, sy0 + 15], fill=(20, 20, 20), width=2)
    d.line([sx0 + 458, sy0 + 15, sx0 + 463, sy0 + 8], fill=(20, 20, 20), width=2)
    d.text((sx0 + 471, sy0 + 5), "推論プロセスを表示", font=F_UI, fill=LABEL)
    d.text((sx0 + 600, sy0 + 5), "ランダム性:", font=F_UI, fill=LABEL)
    field(d, sx0 + 670, sy0, 92, h, "7 (×0.1)", spin=True)
    d.text((sx0 + 776, sy0 + 5), "最大トークン:", font=F_UI, fill=LABEL)
    field(d, sx0 + 862, sy0, 100, h, "10000", spin=True)

    ux0, uy0, _, _ = LAYOUT["usage"]
    d.text((ux0, uy0), USAGE_DONE if stage == "done" else USAGE_IDLE,
           font=F_SMALL, fill=MUTED)

    bx0, by0, bx1, by1 = LAYOUT["btn_row"]
    gap, count = 7, len(BUTTONS)
    bw = ((bx1 - bx0) - gap * (count - 1)) / count
    for i, label in enumerate(BUTTONS):
        state = "normal"
        if label == "送信":
            state = {"press": "pressed", "stream": "disabled"}.get(stage, "normal")
        elif label == "キャンセル":
            state = "normal" if stage == "stream" else "disabled"
        elif label == "再生成":
            state = "normal" if stage == "done" else "disabled"
        button(d, round(bx0 + i * (bw + gap)), by0, round(bw), by1 - by0,
               label, state=state)


def draw_status(d, stage: str):
    x0, y0, x1, y1 = LAYOUT["status"]
    box(d, (x0, y0, x1, y1), BG)
    d.line([x0, y0, x1, y0], fill=(70, 70, 70))
    text = {
        "idle":   "準備完了",
        "press":  "DeepSeek が応答中…",
        "stream": "DeepSeek が応答中…",
        "done":   "完了 ｜ 入力: 3,120 tok ／ 出力: 366 tok"
                  " ｜ 今回 $0.00244 ｜ 累計 $0.00612",
    }[stage]
    d.text((10, y0 + 5), text, font=F_UI, fill=MUTED)


def render(stage: str, reveal: int = 0) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    draw_titlebar(d, dirty=stage != "idle")
    if stage in ("stream", "done"):
        shown = ASSIST_MSG if stage == "done" else ASSIST_MSG[:reveal]
    else:
        shown = None
    draw_conversation(d, shown, streaming=stage == "stream")
    draw_reasoning(d, visible=stage == "done")
    draw_system_prompt(d)
    draw_input_area(d, stage)
    draw_status(d, stage)
    return img


def build():
    frames, durations = [], []

    def add(image, ms):
        frames.append(image)
        durations.append(ms)

    add(render("idle"), 1600)
    add(render("press"), 260)
    step = max(1, len(ASSIST_MSG) // 12)
    for k in range(step, len(ASSIST_MSG), step):
        add(render("stream", k), 150)
    add(render("done"), 3600)

    # UI はほぼ単色なので、共通パレットへ落としても劣化しない。
    # フルカラーのまま書き出すと 600KB を超えて README には重い。
    palette = frames[-1].quantize(colors=64, method=Image.MEDIANCUT)
    reduced = [f.quantize(palette=palette, dither=Image.NONE) for f in frames]

    out = ROOT / "images" / "demo.gif"
    out.parent.mkdir(exist_ok=True)
    reduced[0].save(out, save_all=True, append_images=reduced[1:],
                    duration=durations, loop=0, optimize=True, disposal=1)
    frames[-1].save(ROOT / "images" / "_preview.png")
    print(f"Wrote {out} ({len(reduced)} frames, {out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    build()
