"""Chart images for PDFs"""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg") 
import matplotlib.pyplot as plt  # noqa: E402

from app.schemas import ChartData  # noqa: E402
from renderers import theme  # noqa: E402

PALETTE = ["#" + theme.PRIMARY, "#" + theme.ACCENT, "#F18F01", "#6A994E", "#BC4B51", "#8E7DBE"]


def chart_png(chart: ChartData, width_in: float = 6.2, height_in: float = 3.2, dpi: int = 200) -> bytes:
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=dpi)
    try:
        cats = chart.categories
        if chart.kind == "pie":
            vals = chart.series[0].values
            ax.pie(vals, labels=cats, autopct="%1.0f%%" if sum(vals) > 0 else None, colors=PALETTE * 10,
                   startangle=90, textprops={"fontsize": 8})
            ax.axis("equal")
        elif chart.kind == "bar":
            n = len(chart.series)
            w = 0.8 / n
            for i, s in enumerate(chart.series):
                xs = [x + (i - (n - 1) / 2) * w for x in range(len(cats))]
                ax.bar(xs, s.values, width=w, label=s.name, color=PALETTE[i % len(PALETTE)])
            ax.set_xticks(range(len(cats)))
            ax.set_xticklabels(cats, fontsize=8, rotation=30 if len(cats) > 6 else 0, ha="right" if len(cats) > 6 else "center")
        else:
            for i, s in enumerate(chart.series):
                ax.plot(range(len(cats)), s.values, marker="o", label=s.name, color=PALETTE[i % len(PALETTE)])
            step = max(1, len(cats) // 12)
            ax.set_xticks(range(0, len(cats), step))
            ax.set_xticklabels(cats[::step], fontsize=8, rotation=30 if len(cats) > 6 else 0, ha="right" if len(cats) > 6 else "center")
        if chart.kind != "pie":
            ax.tick_params(axis="y", labelsize=8)
            ax.grid(axis="y", alpha=0.3)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            if len(chart.series) > 1:
                ax.legend(fontsize=8, frameon=False)
        ax.set_title(chart.title, fontsize=10, fontweight="bold", color="#" + theme.PRIMARY)
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)
