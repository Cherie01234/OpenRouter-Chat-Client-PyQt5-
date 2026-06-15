"""Generate a stylized demo GIF of the OpenRouter chat client (Pillow mockup).

    python make_demo_gif.py  ->  images/demo.gif (+ images/_preview.png)
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
W, H = 760, 470
BG = (27, 27, 27)
TITLE_BAR = (45, 45, 45)
PANEL = (24, 24, 24)
INPUT_BG = (42, 42, 42)
BORDER = (85, 85, 85)
TEXT = (220, 220, 220)
MUTED = (150, 150, 150)
USER = (74, 163, 255)
ASSIST = (54, 196, 106)
BTN = (47, 111, 176)

JP_FONTS = ["C:/Windows/Fonts/YuGothR.ttc", "C:/Windows/Fonts/meiryo.ttc",
            "C:/Windows/Fonts/msgothic.ttc", "C:/Windows/Fonts/arial.ttf"]


def font(size):
    for p in JP_FONTS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


F_TITLE, F_BODY, F_SMALL = font(13), font(15), font(12)

USER_MSG = "あなたについて教えてください"
ASSIST_MSG = ("こんにちは！私は DeepSeek です。テキストでの対話、ファイルの読み取り、"
              "長文の要約などが得意です。OpenRouter 経由で複数のモデルを切り替えて利用できます。")


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=fnt) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def render(stage: str, reveal: int = 0):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Title bar
    d.rectangle([0, 0, W, 28], fill=TITLE_BAR)
    d.text((10, 6), "OpenRouter Chat — PyQt5", font=F_TITLE, fill=TEXT)

    # Chat panel
    d.rectangle([8, 34, W - 8, 360], fill=PANEL, outline=BORDER)
    x, y = 20, 46
    d.text((x, y), "あなた:", font=F_BODY, fill=USER)
    for line in wrap(d, USER_MSG, F_BODY, W - 120):
        d.text((x + 64, y), line, font=F_BODY, fill=TEXT)
        y += 24
    y += 12

    if stage in ("stream", "done"):
        d.text((x, y), "DeepSeek:", font=F_BODY, fill=ASSIST)
        shown = ASSIST_MSG if stage == "done" else ASSIST_MSG[:reveal]
        ty = y + 28
        for line in wrap(d, shown, F_BODY, W - 60):
            d.text((x, ty), line, font=F_BODY, fill=TEXT)
            ty += 24

    # Input box
    d.rectangle([8, 372, W - 8, 414], fill=INPUT_BG, outline=BORDER)
    if stage in ("idle", "press"):
        d.text((18, 384), USER_MSG, font=F_BODY, fill=TEXT)
    else:
        d.text((18, 384), "メッセージを入力 … (Ctrl+Enter で送信)", font=F_BODY, fill=MUTED)

    # Model bar + send button
    d.text((12, 426), "モデル: deepseek/deepseek-v4-pro   思考レベル: high   最大トークン: 100000",
           font=F_SMALL, fill=MUTED)
    btn = (35, 90, 150) if stage == "press" else BTN
    d.rounded_rectangle([W - 92, 420, W - 12, 446], 5, fill=btn)
    d.text((W - 72, 426), "送信", font=F_BODY, fill="white")

    # Status
    status = {"idle": "準備完了", "press": "送信中...", "stream": "応答生成中...",
              "done": "完了  |  入力: 10 tok / 出力: 142 tok"}[stage]
    d.text((12, 452), status, font=F_SMALL, fill=MUTED)
    return img


def build():
    frames, durations = [], []

    def add(im, ms):
        frames.append(im)
        durations.append(ms)

    add(render("idle"), 1400)
    add(render("press"), 250)
    step = max(1, len(ASSIST_MSG) // 12)
    for k in range(step, len(ASSIST_MSG), step):
        add(render("stream", k), 130)
    add(render("done"), 3200)

    out = ROOT / "images" / "demo.gif"
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True, disposal=2)
    frames[-1].save(ROOT / "images" / "_preview.png")
    print(f"Wrote {out} ({len(frames)} frames, {out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    build()
