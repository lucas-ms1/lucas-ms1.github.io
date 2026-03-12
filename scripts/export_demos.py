from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import time
import contextlib
import shutil
from pathlib import Path
from typing import Any

from streamlit_capture import dump_widget_calls, run_streamlit_script


ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor"

MACRO_DATA_SUITE = {
    "slug": "macro-data-suite",
    "repo_url": "https://github.com/lucas-ms1/Macro-Project-1-Data-Suite.git",
}
MACRO_MODEL_SOLVER = {
    "slug": "macro-model-solver",
    "repo_url": "https://github.com/lucas-ms1/ECO-317-Project-2---Model-Solver.git",
}


def _run(cmd: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n\nSTDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout.strip()


def ensure_cloned(repo_url: str, *, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    repo_name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    repo_path = dest_dir / repo_name
    if not repo_path.exists():
        print(f"[clone] {repo_url} -> {repo_path}")
        _run(["git", "clone", "--depth", "1", repo_url, str(repo_path)])
    else:
        print(f"[update] {repo_path}")
        try:
            _run(["git", "pull", "--ff-only"], cwd=repo_path)
        except Exception as e:
            print(f"[warn] Could not fast-forward update {repo_path}: {e}")
    return repo_path


def git_head_commit(repo_path: Path) -> str:
    return _run(["git", "rev-parse", "HEAD"], cwd=repo_path)


def find_entrypoint(repo_path: Path) -> Path:
    candidates = [
        repo_path / "app.py",
        repo_path / "streamlit_app.py",
        repo_path / "main.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    for py in sorted(repo_path.glob("*.py")):
        try:
            txt = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "streamlit" in txt:
            return py
    raise FileNotFoundError(f"Could not find Streamlit entrypoint in {repo_path}")


def _safe_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-") or "output"


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


@contextlib.contextmanager
def prepend_sys_path(paths: list[Path]):
    old = list(sys.path)
    try:
        for p in reversed(paths):
            sys.path.insert(0, str(p))
        yield
    finally:
        sys.path[:] = old


def render_df_page(df: Any, *, title: str, subtitle: str | None = None) -> str:
    subtitle_html = f"<p class='muted'>{subtitle}</p>" if subtitle else ""
    table_html = df.to_html(index=True, border=0, classes="df") if hasattr(df, "to_html") else f"<pre>{df}</pre>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{
      --primary: #1b261b;
      --muted: #4f5d4e;
      --bg: #f2f4ef;
      --card-bg: #ffffff;
      --border: #ced6c4;
      --shadow: 0 4px 20px rgba(27, 38, 27, 0.08);
      --pill-bg: #e8ede4;
    }}
    body {{
      font-family: system-ui, -apple-system, Segoe UI, Inter, Arial, sans-serif;
      margin: 0;
      padding: 18px;
      background: var(--bg);
      color: var(--primary);
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      box-shadow: var(--shadow);
    }}
    h1 {{ margin: 0 0 6px 0; font-size: 18px; }}
    .muted {{ color: var(--muted); margin: 0 0 12px 0; font-size: 13px; }}
    .df {{
      border-collapse: collapse;
      width: 100%;
      font-size: 12px;
    }}
    .df th, .df td {{
      border-top: 1px solid var(--pill-bg);
      padding: 6px 8px;
      vertical-align: top;
      text-align: right;
      white-space: nowrap;
    }}
    .df th:first-child, .df td:first-child {{ text-align: left; }}
    .df thead th {{
      position: sticky;
      top: 0;
      background: white;
      z-index: 1;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    {subtitle_html}
    <div style="overflow:auto; max-height: 70vh;">
      {table_html}
    </div>
  </div>
</body>
</html>
"""


def export_capture_to_assets(capture: Any, *, assets_dir: Path, title_prefix: str) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    assets_dir.mkdir(parents=True, exist_ok=True)

    for i, out in enumerate(capture.outputs):
        out_title = out.title or f"{title_prefix} Output {i+1}"
        base = f"{i+1:02d}-{_safe_slug(out_title)}"

        if out.kind == "plotly":
            try:
                html = out.payload.to_html(include_plotlyjs=True, full_html=True)
            except Exception as e:
                html = f"<!doctype html><meta charset='utf-8'><pre>Plotly export failed: {e}</pre>"
            path = assets_dir / f"{base}.html"
            _write_text(path, html)
            outputs.append(
                {
                    "type": "plotly",
                    "title": out_title,
                    "caption": out.meta.get("caption") or "",
                    "path": f"assets/{path.name}",
                    "height": 560,
                }
            )
            continue

        if out.kind in {"dataframe", "table"}:
            path = assets_dir / f"{base}.html"
            page = render_df_page(out.payload, title=out_title, subtitle=out.meta.get("subtitle"))
            _write_text(path, page)
            outputs.append(
                {
                    "type": "table",
                    "title": out_title,
                    "caption": out.meta.get("caption") or "",
                    "path": f"assets/{path.name}",
                    "height": 520,
                }
            )
            continue

        if out.kind == "image":
            try:
                import matplotlib.pyplot as plt  # type: ignore

                fig = out.payload
                png_path = assets_dir / f"{base}.png"
                if fig is None:
                    fig = plt.gcf()
                fig.savefig(png_path, dpi=180, bbox_inches="tight")
                outputs.append(
                    {
                        "type": "image",
                        "title": out_title,
                        "caption": out.meta.get("caption") or "",
                        "path": f"assets/{png_path.name}",
                        "height": 560,
                    }
                )
            except Exception:
                pass
            continue

    return outputs


def choose_buttons_to_press(widget_calls: list[Any]) -> dict[str, bool]:
    include = ("run", "solve", "compute", "generate", "download", "fetch", "load", "estimate", "simulate", "execute")
    exclude = ("refresh", "rerun", "reset", "clear", "delete")
    overrides: dict[str, bool] = {}
    for call in widget_calls:
        kind = getattr(call, "kind", "")
        label = getattr(call, "label", "") or ""
        if kind not in {"button", "form_submit_button"}:
            continue
        l = str(label).strip().lower()
        if not l:
            continue
        if any(x in l for x in exclude):
            continue
        if any(x in l for x in include):
            overrides[str(label)] = True
    return overrides


def choose_empirical_option(options: list[Any]) -> Any | None:
    for opt in options:
        s = str(opt).lower()
        if "empirical" in s or "calibr" in s or "estimated" in s:
            return opt
    return None


def detect_model_widget(widget_calls: list[dict[str, Any]]) -> tuple[str | None, list[Any]]:
    for call in widget_calls:
        label = str(call.get("label") or "")
        options = call.get("options") or []
        if options and len(options) >= 3 and "model" in label.lower():
            return label, options
    return None, []


def detect_preset_widget(widget_calls: list[dict[str, Any]]) -> tuple[str | None, list[Any]]:
    for call in widget_calls:
        label = str(call.get("label") or "")
        options = call.get("options") or []
        if not options or len(options) < 2:
            continue
        l = label.lower()
        if "preset" in l or "calibration" in l or "parameter" in l or "scenario" in l:
            return label, options
    return None, []


def export_macro_data_suite(repo_path: Path, *, site_project_dir: Path, repo_url: str) -> None:
    commit = git_head_commit(repo_path)
    assets_dir = site_project_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Stage outputs first; only replace the published assets on success.
    staging_dir = assets_dir / "_staging"
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # For this project, exporting from the full Streamlit UI is brittle (jobs, reruns, async status).
    # Instead, we run the same underlying pipeline used by the app: fetch a macro series and run
    # the default econ recipes, then export Plotly + tables for embedding.
    src_dir = repo_path / "src"
    with prepend_sys_path([src_dir, repo_path]):
        import pandas as pd  # type: ignore

        from finrec.jobs.runner import JobRunner  # type: ignore
        from finrec.storage.sqlite import SQLiteStorage  # type: ignore
        from finrec.ui.pipelines import submit_provider_fetch, submit_recipe_run, get_jobs_by_id  # type: ignore
        from finrec.viz.plotly_timeseries import plot_timeseries  # type: ignore

        today = dt.date.today()
        start = (today - dt.timedelta(days=365 * 10)).isoformat()
        end = today.isoformat()
        series_id = "CPIAUCSL"

        df_fcst = None
        last_err: Exception | None = None

        for attempt in range(1, 4):
            base = Path(tempfile.mkdtemp(prefix="macro_data_suite_export_"))
            db = base / "finrec.db"
            outdir = base / "results"
            outdir.mkdir(parents=True, exist_ok=True)

            storage = SQLiteStorage(db)
            storage.init_schema()
            runner = JobRunner(storage, outdir)

            try:
                def wait(job_id: str, *, timeout_s: float = 120.0) -> Any:
                    deadline = time.time() + timeout_s
                    while time.time() < deadline:
                        jobs = get_jobs_by_id(storage, limit=2000)
                        j = jobs.get(job_id)
                        if j and getattr(j, "status", None) in {"SUCCEEDED", "FAILED"}:
                            return j
                        time.sleep(0.1)
                    raise TimeoutError(f"Timed out waiting for job {job_id}")

                print(f"[pipeline] fetch macro:{series_id} ({start} -> {end}) [attempt {attempt}/3]")
                fred_job = submit_provider_fetch(
                    runner=runner,
                    kind="macro",
                    provider_id="fred",
                    request={"series_id": series_id, "start_date": start, "end_date": end, "n": 120},
                )

                j_fred = wait(fred_job, timeout_s=180.0)
                if getattr(j_fred, "status", None) != "SUCCEEDED" or not getattr(j_fred, "output_path", None):
                    logs = storage.list_logs(fred_job, limit=40)
                    tail = "\n".join([f"{r.level}: {r.message}" for r in logs[-10:]])
                    raise RuntimeError(
                        f"FRED fetch failed: job={fred_job} status={getattr(j_fred, 'status', None)} "
                        f"error={getattr(j_fred, 'error', None)}\n{tail}"
                    )

                series_csv = staging_dir / f"fred_{series_id}_{start}_to_{end}.csv"
                df_series = pd.read_csv(j_fred.output_path)
                df_series.to_csv(series_csv, index=False)

                print("[pipeline] recipe inflation_yoy")
                infl_job = submit_recipe_run(
                    runner=runner,
                    input_job_id=fred_job,
                    input_path=j_fred.output_path,
                    recipe_id="inflation_yoy",
                    params={"date_col": "date", "value_col": "value", "out_col": "inflation_yoy"},
                )
                j_infl = wait(infl_job, timeout_s=180.0)
                if getattr(j_infl, "status", None) != "SUCCEEDED" or not getattr(j_infl, "output_path", None):
                    raise RuntimeError(
                        f"inflation_yoy failed: job={infl_job} status={getattr(j_infl, 'status', None)}"
                    )
                df_infl = pd.read_csv(j_infl.output_path)
                infl_csv = staging_dir / f"inflation_yoy_{series_id}_{start}_to_{end}.csv"
                df_infl.to_csv(infl_csv, index=False)

                print("[pipeline] recipe rolling_zscore (inflation_yoy)")
                z_job = submit_recipe_run(
                    runner=runner,
                    input_job_id=infl_job,
                    input_path=j_infl.output_path,
                    recipe_id="rolling_zscore",
                    params={"value_col": "inflation_yoy", "window": 12, "prefix": "infl_12m"},
                )
                j_z = wait(z_job, timeout_s=180.0)
                if getattr(j_z, "status", None) != "SUCCEEDED" or not getattr(j_z, "output_path", None):
                    raise RuntimeError(f"rolling_zscore failed: job={z_job} status={getattr(j_z, 'status', None)}")
                df_z = pd.read_csv(j_z.output_path)
                z_csv = staging_dir / f"rolling_zscore_inflation_yoy_{series_id}_{start}_to_{end}.csv"
                df_z.to_csv(z_csv, index=False)

                print("[pipeline] recipe ar1 (inflation_yoy)")
                ar1_job = submit_recipe_run(
                    runner=runner,
                    input_job_id=infl_job,
                    input_path=j_infl.output_path,
                    recipe_id="ar1",
                    params={"y_col": "inflation_yoy"},
                )
                j_ar1 = wait(ar1_job, timeout_s=180.0)
                if getattr(j_ar1, "status", None) != "SUCCEEDED" or not getattr(j_ar1, "output_path", None):
                    raise RuntimeError(f"ar1 failed: job={ar1_job} status={getattr(j_ar1, 'status', None)}")
                df_ar1 = pd.read_csv(j_ar1.output_path)

                # Optional: ARIMA forecast (may require optional deps)
                df_fcst = None
                try:
                    print("[pipeline] recipe forecast_arima (inflation_yoy)")
                    fcst_job = submit_recipe_run(
                        runner=runner,
                        input_job_id=infl_job,
                        input_path=j_infl.output_path,
                        recipe_id="forecast_arima",
                        params={"date_col": "date", "y_col": "inflation_yoy", "horizon": 12, "test_size": 0.2},
                    )
                    j_fcst = wait(fcst_job, timeout_s=240.0)
                    if getattr(j_fcst, "output_path", None):
                        df_fcst = pd.read_csv(j_fcst.output_path)
                except Exception as e:
                    print(f"[warn] forecast_arima skipped: {e}")

                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(1.5 * attempt)
            finally:
                runner._executor.shutdown(wait=True)  # type: ignore[attr-defined]

        if last_err is not None:
            raise last_err

        # Build Plotly figures
        figs: list[tuple[str, Any]] = []
        try:
            figs.append(
                (
                    "CPI level (CPIAUCSL)",
                    plot_timeseries(df_series, ["value"], title="CPIAUCSL (level)", x_col="date", y_left_label="Index"),
                )
            )
        except Exception as e:
            print(f"[warn] could not plot CPI level: {e}")

        try:
            figs.append(
                (
                    "Inflation YoY (%)",
                    plot_timeseries(
                        df_infl,
                        ["inflation_yoy"],
                        title="Inflation YoY (%) from CPIAUCSL",
                        x_col="date",
                        y_left_label="Percent",
                    ),
                )
            )
        except Exception as e:
            print(f"[warn] could not plot inflation_yoy: {e}")

        try:
            if "infl_12m_z" in df_z.columns:
                figs.append(
                    (
                        "Inflation YoY (z-score, 12m rolling)",
                        plot_timeseries(
                            df_z,
                            ["infl_12m_z"],
                            title="Inflation YoY — rolling z-score (12m)",
                            x_col="date",
                            y_left_label="z",
                        ),
                    )
                )
        except Exception as e:
            print(f"[warn] could not plot z-score: {e}")

        if df_fcst is not None and not df_fcst.empty:
            try:
                import plotly.graph_objects as go  # type: ignore
            except Exception:
                go = None
            try:
                if go is not None:
                    dfp = df_fcst.copy()
                    # Plot train/test actuals + predictions + forecast
                    dfp["date"] = pd.to_datetime(dfp["date"], errors="coerce")
                    fig = go.Figure()
                    df_train = dfp[dfp["segment"] == "train"]
                    df_test = dfp[dfp["segment"] == "test"]
                    df_fore = dfp[dfp["segment"] == "forecast"]
                    fig.add_trace(go.Scatter(x=df_train["date"], y=df_train["y"], mode="lines", name="train (y)"))
                    if not df_test.empty:
                        fig.add_trace(go.Scatter(x=df_test["date"], y=df_test["y"], mode="lines", name="test (y)"))
                        fig.add_trace(
                            go.Scatter(x=df_test["date"], y=df_test["yhat"], mode="lines", name="test (yhat)")
                        )
                    if not df_fore.empty:
                        fig.add_trace(
                            go.Scatter(x=df_fore["date"], y=df_fore["yhat"], mode="lines", name="forecast (yhat)")
                        )
                    fig.update_layout(
                        title="ARIMA forecast (inflation_yoy)",
                        hovermode="x unified",
                        margin=dict(l=40, r=40, t=50, b=40),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
                    )
                    figs.append(("ARIMA forecast (inflation_yoy)", fig))
            except Exception as e:
                print(f"[warn] could not plot forecast: {e}")

        outputs: list[dict[str, Any]] = []
        for idx, (title, fig) in enumerate(figs, start=1):
            fname = f"{idx:02d}-{_safe_slug(title)}.html"
            html = fig.to_html(include_plotlyjs=True, full_html=True)
            _write_text(staging_dir / fname, html)
            outputs.append(
                {
                    "type": "plotly",
                    "title": title,
                    "caption": "",
                    "path": f"assets/{fname}",
                    "height": 560,
                }
            )

        # Summary tables
        summary = df_infl[["inflation_yoy"]].describe().round(4) if "inflation_yoy" in df_infl.columns else df_series.describe()
        summary_page = render_df_page(summary, title="Summary statistics", subtitle="Describe() on inflation_yoy")
        _write_text(staging_dir / "summary-stats.html", summary_page)
        outputs.append(
            {
                "type": "table",
                "title": "Summary statistics",
                "caption": "",
                "path": "assets/summary-stats.html",
                "height": 520,
            }
        )

        ar1_page = render_df_page(df_ar1, title="AR(1) results", subtitle="Recipe output table")
        _write_text(staging_dir / "ar1-results.html", ar1_page)
        outputs.append(
            {
                "type": "table",
                "title": "AR(1) fit (inflation_yoy)",
                "caption": "",
                "path": "assets/ar1-results.html",
                "height": 520,
            }
        )

        snapshot_paths = [
            f"assets/{series_csv.name}",
            f"assets/{infl_csv.name}",
            f"assets/{z_csv.name}",
        ]

    manifest = {
        "project": "Macro Data Suite",
        "slug": "macro-data-suite",
        "repo_url": repo_url,
        "repo_commit": commit,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scenario_notes": "Pipeline export: FRED CPIAUCSL over the last 365 days; recipes: inflation_yoy, rolling_zscore (12m), ar1, optional forecast_arima.",
        "data_snapshots": snapshot_paths,
        "widget_values": {
            "mode": "Econ",
            "series_id": "CPIAUCSL",
            "start_date": start,
            "end_date": end,
        },
        "outputs": outputs,
    }
    _write_json(staging_dir / "manifest.json", manifest)

    # Swap staging -> published assets (keep placeholder.html)
    for p in assets_dir.glob("*"):
        if p.name in {"placeholder.html", "_staging"}:
            continue
        if p.is_dir():
            continue
        p.unlink(missing_ok=True)
    for p in staging_dir.glob("*"):
        shutil.move(str(p), str(assets_dir / p.name))
    shutil.rmtree(staging_dir, ignore_errors=True)


def export_macro_model_solver(repo_path: Path, *, site_project_dir: Path, repo_url: str) -> None:
    entry = find_entrypoint(repo_path)
    commit = git_head_commit(repo_path)
    assets_dir = site_project_dir / "assets"
    extra_paths = [repo_path]
    if (repo_path / "src").exists():
        extra_paths.append(repo_path / "src")

    print(f"[inspect] {entry}")
    inspection = run_streamlit_script(entry, press_all_buttons=False, record_widgets=True, extra_sys_path=extra_paths)
    widget_calls = json.loads(dump_widget_calls(inspection.widget_calls))

    model_label, model_options = detect_model_widget(widget_calls)
    preset_label, preset_options = detect_preset_widget(widget_calls)
    empirical = choose_empirical_option(preset_options) if preset_options else None

    models: list[dict[str, Any]] = []
    if model_label and model_options:
        button_overrides = choose_buttons_to_press(inspection.widget_calls)
        for opt in model_options:
            overrides: dict[str, Any] = {model_label: opt}
            if preset_label and empirical is not None:
                overrides[preset_label] = empirical
            overrides.update(button_overrides)
            print(f"[run] model={opt} preset={empirical if empirical is not None else '(default)'}")
            cap = run_streamlit_script(
                entry, overrides=overrides, press_all_buttons=False, record_widgets=True, extra_sys_path=extra_paths
            )
            outs = export_capture_to_assets(cap, assets_dir=assets_dir, title_prefix=str(opt))
            models.append(
                {
                    "id": _safe_slug(str(opt)),
                    "label": str(opt),
                    "parameters": cap.widget_values,
                    "outputs": outs,
                }
            )
    else:
        print("[warn] Could not detect a model selector widget; exporting a single default snapshot.")
        cap = run_streamlit_script(entry, press_all_buttons=False, record_widgets=True, extra_sys_path=extra_paths)
        outs = export_capture_to_assets(cap, assets_dir=assets_dir, title_prefix="Macro Model Solver")
        models.append({"id": "default", "label": "Default", "parameters": cap.widget_values, "outputs": outs})

    manifest = {
        "project": "Macro Model Solver",
        "slug": "macro-model-solver",
        "repo_url": repo_url,
        "repo_commit": commit,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scenario_notes": "Empirical/calibrated preset when detectable; otherwise default widget values.",
        "detected": {
            "model_widget_label": model_label,
            "preset_widget_label": preset_label,
            "empirical_option": str(empirical) if empirical is not None else None,
        },
        "models": models,
    }
    _write_json(assets_dir / "manifest.json", manifest)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Export static demo assets for GitHub Pages.")
    ap.add_argument("--all", action="store_true", help="Export both projects.")
    ap.add_argument("--macro-data-suite", action="store_true", help="Export Macro Data Suite demo.")
    ap.add_argument("--macro-model-solver", action="store_true", help="Export Macro Model Solver demo.")
    ap.add_argument("--vendor-dir", type=Path, default=VENDOR_DIR, help="Where to clone repos (gitignored).")
    ap.add_argument("--print-widgets", action="store_true", help="Print detected widgets (model solver) and exit.")
    args = ap.parse_args(argv)

    do_data = args.all or args.macro_data_suite
    do_solver = args.all or args.macro_model_solver
    if not (do_data or do_solver or args.print_widgets):
        ap.error("Choose --all or a specific project flag.")

    if args.print_widgets:
        repo_path = ensure_cloned(MACRO_MODEL_SOLVER["repo_url"], dest_dir=args.vendor_dir)
        entry = find_entrypoint(repo_path)
        cap = run_streamlit_script(entry, press_all_buttons=True, record_widgets=True, extra_sys_path=[repo_path])
        print(dump_widget_calls(cap.widget_calls))
        return 0

    if do_data:
        repo_path = ensure_cloned(MACRO_DATA_SUITE["repo_url"], dest_dir=args.vendor_dir)
        export_macro_data_suite(
            repo_path, site_project_dir=ROOT / "projects" / MACRO_DATA_SUITE["slug"], repo_url=MACRO_DATA_SUITE["repo_url"]
        )

    if do_solver:
        repo_path = ensure_cloned(MACRO_MODEL_SOLVER["repo_url"], dest_dir=args.vendor_dir)
        export_macro_model_solver(
            repo_path,
            site_project_dir=ROOT / "projects" / MACRO_MODEL_SOLVER["slug"],
            repo_url=MACRO_MODEL_SOLVER["repo_url"],
        )

    print("[done] Export complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
