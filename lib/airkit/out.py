"""指令輸出的上限：超過就把完整內容寫進 .air/out/<指令>.txt，畫面只印開頭與檔案位置。

試跑（2026-10-24）：Claude 的 AIR 指令 50 次裡 43 次自己加了 | head 或 | tail，Codex 151 次裡只有 11 次；
Codex 把整段輸出（review --bundle 一次近 2 萬字、規則檔、AIR 的原始碼）都讀進對話，之後每一次呼叫都背著它。
上限由程式決定，不靠 AI 記得截斷。--json 的輸出給程式讀，不截；hook 的輸出各自有上限（Stop 最多 10 組）。
"""

from __future__ import annotations

from pathlib import Path

from .checks import rel
from .config import Config

OUT_DIR = Path(".air") / "out"  # 不進版本控制；每個指令只留最近一次


def emit(cfg: Config, name: str, text: str) -> str:
    """text 在上限內就原樣回傳；超過就寫進 .air/out/<name>.txt，回傳開頭（切在換行）加上完整內容的位置。上限 0＝不截。"""
    limit = cfg.output_max_chars
    if limit <= 0 or len(text) <= limit:
        return text
    path = cfg.root / OUT_DIR / f"{name}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / ".gitignore").write_text("*\n", encoding="utf-8")  # 2.7 以前裝的專案 .air/.gitignore 裡沒有 out，自己忽略自己
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    cut = text.rfind("\n", 0, limit)
    head = text[:cut] if cut > limit // 2 else text[:limit]
    return (f"{head}\n…（只印前 {len(head):,} 字，共 {len(text):,} 字；完整內容在 {rel(path, cfg.root)}，"
            f"用 sed -n '起,迄p' 只讀需要的那幾行，不要整份讀進對話；上限是 .air/project.toml 的 [output] max-chars）")
