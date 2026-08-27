"""agent/spatial_index/benchmark.py — Linear scan vs STRtree, for real.

Outline section 12 is explicit: don't assume a data-structure upgrade is
faster, measure it. This script:

  1. Starts from the project's actual data scale — 80 real points (48 study
     rooms + 32 POIs; see geo_data.py) — this is the size the app has TODAY.
     The first scale checkpoint below is 91, not 80: it pads the 80 real
     points with 11 synthetic ones (see step 2) to land on a round-ish
     number close to, but not exactly, today's real scale. Every larger
     checkpoint is majority-synthetic.
  2. Adds synthetic points (uniformly distributed inside the real campus
     bounding box from geo_data.campus_bbox()) to sweep up to 20,000 points,
     simulating what a campus-scale POI dataset (all of ZJU, or a
     city-scale deployment) would look like.
  3. Times both SpatialIndex backends on the *same* query workload at each
     scale, with repeated queries to get a stable mean.
  4. Writes results.json + a chart. Numbers are whatever they actually are
     — this file does not editorialize about which backend "wins" beyond
     reporting the measured ratio.

Run directly: `python -m agent.spatial_index.benchmark`
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.config import get_settings
from geo_data import PointRecord, SpatialIndex, campus_bbox, load_point_records

OUTPUT_DIR = Path(__file__).resolve().parent / "results"


@dataclass
class BenchmarkRow:
    n_points: int
    backend: str
    operation: str
    mean_ms: float
    n_queries: int


def _synthetic_points(n: int, seed: int = 42) -> list[PointRecord]:
    min_lon, min_lat, max_lon, max_lat = campus_bbox(margin_deg=0)
    rng = random.Random(seed)
    return [
        PointRecord(id=f"synthetic_{i}", name=f"synthetic_{i}", lon=rng.uniform(min_lon, max_lon), lat=rng.uniform(min_lat, max_lat), dataset="synthetic")
        for i in range(n)
    ]


def _base_records() -> list[PointRecord]:
    return load_point_records("study_rooms") + load_point_records("pois")


def run_benchmark(
    scales: list[int] = (91, 500, 1000, 2500, 5000, 10000, 20000),
    n_queries: int = 200,
    radius_m: float = 500,
    k: int = 10,
    seed: int = 42,
) -> list[BenchmarkRow]:
    base = _base_records()
    min_lon, min_lat, max_lon, max_lat = campus_bbox(margin_deg=0)
    rng = random.Random(seed)
    query_points = [(rng.uniform(min_lat, max_lat), rng.uniform(min_lon, max_lon)) for _ in range(n_queries)]

    rows: list[BenchmarkRow] = []
    for n in scales:
        if n <= len(base):
            records = base[:n]
        else:
            records = base + _synthetic_points(n - len(base), seed=seed)

        for backend in ("linear", "rtree"):
            index = SpatialIndex(records, backend=backend)  # type: ignore[arg-type]

            start = time.perf_counter()
            for lat, lon in query_points:
                index.query_radius(lat, lon, radius_m)
            elapsed = time.perf_counter() - start
            rows.append(BenchmarkRow(n_points=n, backend=backend, operation="radius", mean_ms=(elapsed / n_queries) * 1000, n_queries=n_queries))

            start = time.perf_counter()
            for lat, lon in query_points:
                index.query_nearest(lat, lon, k)
            elapsed = time.perf_counter() - start
            rows.append(BenchmarkRow(n_points=n, backend=backend, operation="nearest_k", mean_ms=(elapsed / n_queries) * 1000, n_queries=n_queries))

    return rows


def save_results(rows: list[BenchmarkRow]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "results.json"
    out_path.write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def render_chart(rows: list[BenchmarkRow]) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    from agent.tools.chart_tool import _register_cjk_font  # reuse the same font-registration fix
    import matplotlib.pyplot as plt

    plt.rcParams["font.sans-serif"] = _register_cjk_font()
    plt.rcParams["axes.unicode_minus"] = False

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), dpi=140)
    fig.patch.set_facecolor("#f9f7f4")

    for ax, operation, title in zip(axes, ["radius", "nearest_k"], ["缓冲区查询 (radius=500m)", f"最近邻查询 (k=10)"]):
        for backend, color, label in [("linear", "#c2644f", "线性扫描"), ("rtree", "#457b8c", "STRtree")]:
            xs = sorted({r.n_points for r in rows})
            ys = [next(r.mean_ms for r in rows if r.n_points == x and r.backend == backend and r.operation == operation) for x in xs]
            ax.plot(xs, ys, marker="o", color=color, label=label, linewidth=2)
        ax.set_xlabel("数据规模（点位数）")
        ax.set_ylabel("平均查询耗时 (ms)")
        ax.set_title(title, fontsize=11)
        ax.set_yscale("log")
        ax.legend(fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(alpha=0.2)

    fig.suptitle("线性扫描 vs STRtree：真实基准测试结果", fontsize=13)
    fig.tight_layout()
    out_path = OUTPUT_DIR / "benchmark_chart.png"
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    print("Running spatial index benchmark (linear scan vs STRtree)...")
    rows = run_benchmark()
    results_path = save_results(rows)
    chart_path = render_chart(rows)

    print(f"\n{'n_points':>10} {'backend':>8} {'operation':>10} {'mean_ms':>10}")
    for r in rows:
        print(f"{r.n_points:>10} {r.backend:>8} {r.operation:>10} {r.mean_ms:>10.4f}")

    print(f"\nResults: {results_path}")
    print(f"Chart:   {chart_path}")

    # A quick honest speedup summary at the largest scale tested.
    largest_n = max(r.n_points for r in rows)
    for op in ("radius", "nearest_k"):
        linear_ms = next(r.mean_ms for r in rows if r.n_points == largest_n and r.backend == "linear" and r.operation == op)
        rtree_ms = next(r.mean_ms for r in rows if r.n_points == largest_n and r.backend == "rtree" and r.operation == op)
        print(f"At n={largest_n}, {op}: linear={linear_ms:.4f}ms rtree={rtree_ms:.4f}ms -> {linear_ms / rtree_ms:.1f}x")
