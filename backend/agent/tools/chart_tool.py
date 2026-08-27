"""agent/tools/chart_tool.py — generate_chart(data, chart_type).

Fully real (no mocking needed): renders with matplotlib, saves a PNG under
agent_outputs/charts/, and returns both a relative path FastAPI serves via
StaticFiles (see main.py mount) and a base64 thumbnail so the frontend can
show the chart immediately without a second round trip.
"""
from __future__ import annotations

import base64
import glob
import io
import uuid
from typing import Any, Literal

import matplotlib

matplotlib.use("Agg")  # headless — no display server in this environment
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from agent.config import get_settings
from agent.tools.registry import ToolSpec

_PALETTE = ["#457b8c", "#c2644f", "#6b8a5e", "#8b7355", "#b0823c"]


def _register_cjk_font() -> list[str]:
    """matplotlib's bundled font cache frequently never learns about a CJK
    font that got installed at the OS level after matplotlib itself was
    installed — and for multi-face .ttc collections (one file containing
    JP/KR/SC/TC/HK variants) matplotlib's parser only reads the *first*
    face's name, which is often "Noto Sans CJK JP" even on a Simplified
    Chinese system. We register every .ttc/.ttf we can find and return every
    plausible family name so rcParams has a real match instead of silently
    falling back to DejaVu Sans (which has no CJK glyphs at all — discovered
    this by testing chart generation, not by assumption).
    """
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        *glob.glob("/usr/share/fonts/**/NotoSansCJK*.ttc", recursive=True),
        *glob.glob("/usr/share/fonts/**/*PingFang*.tt?", recursive=True),
        *glob.glob("/usr/share/fonts/**/*Microsoft*YaHei*.tt?", recursive=True),
        *glob.glob("/usr/share/fonts/**/wqy*.tt?", recursive=True),
    ]
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            fm.fontManager.addfont(path)
        except (FileNotFoundError, RuntimeError):
            continue
    return [
        "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK TC",
        "PingFang SC", "Microsoft YaHei", "WenQuanYi Zen Hei",
        "Source Han Sans SC", "DejaVu Sans",
    ]


plt.rcParams["font.sans-serif"] = _register_cjk_font()
plt.rcParams["axes.unicode_minus"] = False


def _extract_series(data: Any) -> tuple[list[str], list[float]]:
    """Accepts either a list of {label, value} dicts or a dict of {label: value}."""
    if isinstance(data, dict) and "series" in data:
        data = data["series"]

    if isinstance(data, dict):
        labels = [str(k) for k in data.keys()]
        values = [float(v) for v in data.values()]
        return labels, values

    if isinstance(data, list):
        labels, values = [], []
        for item in data:
            if isinstance(item, dict):
                label = item.get("label") or item.get("name") or item.get("year") or ""
                value = item.get("value")
                if value is None:
                    value = item.get("ndvi_mean")
                labels.append(str(label))
                values.append(float(value) if value is not None else 0.0)
        return labels, values

    raise ValueError("data must be a list of {label,value} objects or a {label: value} mapping")


def generate_chart(
    data: Any,
    chart_type: Literal["bar", "line", "pie"] = "bar",
    title: str = "",
    x_label: str = "",
    y_label: str = "",
) -> dict[str, Any]:
    labels, values = _extract_series(data)
    if not labels:
        return {"ok": False, "message": "没有可绘制的数据"}

    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(6, 4), dpi=140)
    fig.patch.set_facecolor("#f9f7f4")
    ax.set_facecolor("#ffffff")

    if chart_type == "bar":
        ax.bar(labels, values, color=_PALETTE[0])
        ax.spines[["top", "right"]].set_visible(False)
    elif chart_type == "line":
        ax.plot(labels, values, marker="o", color=_PALETTE[1], linewidth=2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.25)
    elif chart_type == "pie":
        ax.pie(values, labels=labels, colors=_PALETTE, autopct="%1.0f%%", textprops={"fontsize": 9})
    else:
        plt.close(fig)
        return {"ok": False, "message": f"不支持的图表类型: {chart_type}"}

    if title:
        ax.set_title(title, fontsize=12, color="#3b3a39", pad=12)
    if x_label and chart_type != "pie":
        ax.set_xlabel(x_label, fontsize=10)
    if y_label and chart_type != "pie":
        ax.set_ylabel(y_label, fontsize=10)
    if chart_type != "pie" and len(labels) > 6:
        plt.setp(ax.get_xticklabels(), rotation=35, ha="right", fontsize=8)

    fig.tight_layout()

    settings = get_settings()
    filename = f"chart_{uuid.uuid4().hex[:10]}.png"
    out_path = settings.charts_output_dir / filename
    fig.savefig(out_path, facecolor=fig.get_facecolor())

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    thumbnail_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return {
        "ok": True,
        "chart_type": chart_type,
        "title": title,
        "file_path": str(out_path),
        "url_path": f"/agent-outputs/charts/{filename}",
        "thumbnail_base64": f"data:image/png;base64,{thumbnail_b64}",
        "n_points": len(labels),
    }


SPEC = ToolSpec(
    name="generate_chart",
    description=(
        "将结构化数据渲染为图表（柱状图/折线图/饼图）并保存为PNG，返回可访问的URL路径与base64缩略图，"
        "用于把 NDVI 趋势、缓冲区统计等结果转成用户可读的可视化呈现。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "data": {
                "description": "图表数据：{label: value} 映射，或 [{label, value}, ...] 数组（NDVI结果可直接传 series，字段名 year/ndvi_mean 会被自动识别）",
            },
            "chart_type": {"type": "string", "enum": ["bar", "line", "pie"], "default": "bar"},
            "title": {"type": "string", "default": ""},
            "x_label": {"type": "string", "default": ""},
            "y_label": {"type": "string", "default": ""},
        },
        "required": ["data"],
    },
    handler=generate_chart,
)
