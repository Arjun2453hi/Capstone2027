"""visualization.py — the required PNG sanity-check plot (claude.md
Section 6): the depth curve and the cut points together, so a human can
glance at both rather than trusting the numbers blindly.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Union

import matplotlib

matplotlib.use("Agg")  # headless -- this project never needs an interactive window
import matplotlib.pyplot as plt
import numpy as np

from .schema import Topic


def plot_segmentation(
    combined_depth: np.ndarray,
    threshold,
    boost_mask: List[bool],
    topics: List[Topic],
    out_path: Union[str, Path],
) -> None:
    """`threshold` accepts either a single scalar (drawn as one flat
    reference line, for backward compatibility / small synthetic decks)
    or a per-position array (segmenter.py's locally-adaptive threshold,
    drawn as its own curve) -- see segmenter.py's LOCAL_THRESHOLD_WINDOW
    comment for why a single global cutoff under-detects real
    boundaries near a deck's edges."""
    n_boundaries = len(combined_depth)
    x = np.arange(n_boundaries)

    fig, ax = plt.subplots(figsize=(max(12, n_boundaries * 0.05), 6))
    ax.plot(x, combined_depth, color="steelblue", linewidth=1, label="combined multi-scale depth")

    if np.ndim(threshold) > 0:
        ax.plot(x, np.asarray(threshold), color="gray", linestyle=":", linewidth=1, label="local adaptive threshold")
    else:
        ax.axhline(threshold, color="gray", linestyle=":", linewidth=1, label=f"adaptive threshold ({threshold:.3f})")

    # topics[0] starts at the deck's first slide -- not a real cut, skip it.
    for topic in topics[1:]:
        boundary_i = topic.start_slide_id - 1  # the boundary immediately before this topic's first slide
        boosted = bool(boost_mask[boundary_i]) if 0 <= boundary_i < len(boost_mask) else False
        color = "darkorange" if boosted else "crimson"
        ax.axvline(boundary_i, color=color, linestyle="--", linewidth=1)
        ymax = ax.get_ylim()[1]
        ax.text(
            boundary_i, ymax * 0.95, f"{topic.boundary_confidence:.2f}",
            rotation=90, fontsize=7, color=color, va="top", ha="right",
        )

    # Legend proxies for the two boundary colors (axvline calls above
    # don't carry a legend-friendly single label).
    ax.plot([], [], color="crimson", linestyle="--", label="content-only boundary")
    ax.plot([], [], color="darkorange", linestyle="--", label="structurally-boosted boundary")

    ax.set_xlabel("slide position (boundary between slide i and slide i+1)")
    ax.set_ylabel("combined multi-scale depth score")
    ax.set_title("Topic segmentation: depth signal and predicted boundaries")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
