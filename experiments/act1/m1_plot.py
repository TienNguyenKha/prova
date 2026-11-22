"""M1 step 3: plot token usage per turn from the CSV written by `m1_chat.py --auto`.

Run by hand (no API call, no key needed):
  uv run python experiments/act1/m1_plot.py [path/to/usage.csv]

Output: experiments/act1/results/m1_tokens.png (tracked, so LEARNINGS.md can embed it).
"""

import csv
import sys
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.axes import Axes

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "experiments" / "runs" / "m1_usage_auto.csv"
OUT_PNG = ROOT / "experiments" / "act1" / "results" / "m1_tokens.png"

# Reference palette, light mode: categorical slots 1 and 2 (validated adjacent pair) and neutral chrome.
SURFACE = "#fcfcfb"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES_INPUT = "#2a78d6"
SERIES_OUTPUT = "#eb6834"


def read_usage(path: Path) -> tuple[list[int], list[int], list[int]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return (
        [int(r["turn"]) for r in rows],
        [int(r["input_tokens"]) for r in rows],
        [int(r["output_tokens"]) for r in rows],
    )


def style(ax: Axes, title: str) -> None:
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK_PRIMARY, fontsize=11, fontweight="bold")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(BASELINE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)  # recessive hairline grid, horizontal only
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.set_xlabel("turn", color=INK_SECONDARY, fontsize=9)
    ax.set_ylabel("tokens", color=INK_SECONDARY, fontsize=9)


def draw_line(ax: Axes, turns: list[int], values: list[int], color: str, label: str) -> None:
    # 2px-ish line, markers with a surface-colored ring so overlapping marks stay separable.
    ax.plot(
        turns, values, color=color, linewidth=2, marker="o", markersize=6,
        markeredgecolor=SURFACE, markeredgewidth=1.5, label=label,
    )  # fmt: skip
    # Direct label on the last point only, in ink (not series color); the color marker carries identity.
    ax.annotate(
        f"{label} {values[-1]:,}", xy=(turns[-1], values[-1]), xytext=(8, 0),
        textcoords="offset points", va="center", fontsize=9, color=INK_PRIMARY,
    )  # fmt: skip


def main() -> None:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    turns, inputs, outputs = read_usage(csv_path)
    cumulative = [sum(inputs[: i + 1]) for i in range(len(inputs))]

    plt.switch_backend("Agg")  # we only save a file; no window
    fig, (left, right) = plt.subplots(1, 2, figsize=(11.5, 4.4), facecolor=SURFACE)

    style(left, "Tokens per turn: input grows with history, output stays flat")
    draw_line(left, turns, inputs, SERIES_INPUT, "input")
    draw_line(left, turns, outputs, SERIES_OUTPUT, "output")
    left.legend(frameon=False, labelcolor=INK_SECONDARY, fontsize=9, loc="upper left")

    style(right, "Cumulative input tokens: the bill grows faster than the chat")
    draw_line(right, turns, cumulative, SERIES_INPUT, "cumulative input")

    for ax in (left, right):
        ax.set_xticks(turns)
        ax.set_xlim(0.5, turns[-1] + 4.5)  # room for the direct labels on the right
        ax.set_ylim(bottom=0)
    right.yaxis.set_major_formatter(lambda v, _: f"{int(v):,}")
    left.yaxis.set_major_formatter(lambda v, _: f"{int(v):,}")

    fig.tight_layout()
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=150, facecolor=SURFACE)
    print(f"Saved {OUT_PNG} from {csv_path} ({len(turns)} turns)")


if __name__ == "__main__":
    main()
