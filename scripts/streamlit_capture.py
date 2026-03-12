from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import runpy
import sys
import types
from pathlib import Path
from typing import Any, Callable, Iterable


@dataclasses.dataclass(frozen=True)
class WidgetCall:
    kind: str
    label: str
    key: str | None
    options: list[Any] | None = None
    default: Any | None = None


@dataclasses.dataclass
class CapturedOutput:
    kind: str  # "plotly" | "dataframe" | "table" | "markdown" | "text" | "image"
    title: str | None
    payload: Any
    meta: dict[str, Any]


def _normalize_options(options: Any) -> list[Any]:
    if options is None:
        return []
    if isinstance(options, dict):
        return list(options.keys())
    if isinstance(options, (list, tuple)):
        return list(options)
    return [options]


class _OverrideResolver:
    def __init__(self, overrides: dict[str, Any] | None):
        self._overrides = overrides or {}

    def resolve(self, *, kind: str, label: str, key: str | None, options: list[Any], default: Any) -> Any:
        # Priority: key exact -> label exact -> kind:label -> fallback default
        if key and key in self._overrides:
            return self._overrides[key]
        if label in self._overrides:
            return self._overrides[label]
        composite = f"{kind}:{label}"
        if composite in self._overrides:
            return self._overrides[composite]
        return default


class StreamlitCapture:
    def __init__(
        self,
        *,
        overrides: dict[str, Any] | None = None,
        press_all_buttons: bool = False,
        record_widgets: bool = True,
    ) -> None:
        self.outputs: list[CapturedOutput] = []
        self.widget_calls: list[WidgetCall] = []
        self.widget_values: dict[str, Any] = {}
        self._resolver = _OverrideResolver(overrides)
        self._press_all_buttons = press_all_buttons
        self._record_widgets = record_widgets

    def _record_widget(self, call: WidgetCall) -> None:
        if self._record_widgets:
            self.widget_calls.append(call)

    def _set_widget_value(self, label: str, key: str | None, value: Any) -> None:
        if key:
            self.widget_values[key] = value
        self.widget_values[label] = value

    def _capture(self, kind: str, payload: Any, *, title: str | None = None, **meta: Any) -> None:
        self.outputs.append(CapturedOutput(kind=kind, title=title, payload=payload, meta=dict(meta)))

    def _default_choice(self, options: list[Any], index: int | None = None) -> Any:
        if not options:
            return None
        if index is None:
            return options[0]
        try:
            return options[index]
        except Exception:
            return options[0]

    # ----- Display primitives -----
    def title(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Title")

    def header(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Header")

    def subheader(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Subheader")

    def markdown(self, text: str, **_: Any) -> None:
        self._capture("markdown", text)

    def caption(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Caption")

    def code(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Code")

    def latex(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="LaTeX")

    def write(self, obj: Any, **_: Any) -> None:
        try:
            mod = getattr(obj, "__class__", type(obj)).__module__
        except Exception:
            mod = ""

        is_plotly = hasattr(obj, "to_plotly_json") or (isinstance(mod, str) and mod.startswith("plotly"))
        if is_plotly:
            self._capture("plotly", obj)
            return

        is_pandas = isinstance(mod, str) and (mod.startswith("pandas") or mod.startswith("pandas.io"))
        if is_pandas and hasattr(obj, "to_html"):
            self._capture("table", obj)
            return

        self._capture("text", str(obj))

    def warning(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Warning")

    def info(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Info")

    def error(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Error")

    def success(self, text: str, **_: Any) -> None:
        self._capture("text", text, title="Success")

    # ----- Tables / charts -----
    def dataframe(self, df: Any, **_: Any) -> None:
        self._capture("dataframe", df)

    def table(self, df: Any, **_: Any) -> None:
        self._capture("table", df)

    def plotly_chart(self, fig: Any, **_: Any) -> None:
        self._capture("plotly", fig)

    def pyplot(self, fig: Any = None, **_: Any) -> None:
        self._capture("image", fig, format="matplotlib")

    def image(self, image: Any, **_: Any) -> None:
        self._capture("image", image)

    # ----- Layout -----
    def columns(self, spec: int | Iterable[int], **_: Any) -> list["_Container"]:
        n = spec if isinstance(spec, int) else len(list(spec))
        return [_Container(self, container_id=f"col:{i}") for i in range(n)]

    def divider(self, **_: Any) -> None:
        return None

    def metric(self, label: str, value: Any = None, delta: Any = None, **_: Any) -> None:
        self._capture("text", f"{label}: {value} ({delta})", title="Metric")

    def progress(self, value: Any = 0, **_: Any) -> "_Progress":
        self._capture("text", f"Progress: {value}", title="Progress")
        return _Progress(self)

    @contextlib.contextmanager
    def expander(self, label: str, expanded: bool = False, **_: Any):
        _ = (label, expanded)
        yield _Container(self, container_id=f"expander:{label}")

    def tabs(self, tabs: list[str], **_: Any) -> list["_Container"]:
        return [_Container(self, container_id=f"tab:{label}") for label in tabs]

    @contextlib.contextmanager
    def form(self, key: str | None = None, **_: Any):
        yield _Container(self, container_id=f"form:{key or 'form'}")

    def form_submit_button(self, label: str, key: str | None = None, **kwargs: Any) -> bool:
        # Treat like a button with distinct kind so it shows up in widget inspection.
        default_value = self._press_all_buttons
        self._record_widget(WidgetCall(kind="form_submit_button", label=label, key=key, options=None, default=default_value))
        resolved = self._resolver.resolve(
            kind="form_submit_button", label=label, key=key, options=[], default=default_value
        )
        self._set_widget_value(label, key, resolved)
        _ = kwargs
        return bool(resolved)

    @contextlib.contextmanager
    def spinner(self, text: str = "", **_: Any):
        _ = text
        yield _Container(self, container_id="spinner")

    # ----- Inputs (widgets) -----
    def selectbox(
        self,
        label: str,
        options: Any,
        index: int = 0,
        format_func: Callable[[Any], str] | None = None,
        key: str | None = None,
        **_: Any,
    ) -> Any:
        opts = _normalize_options(options)
        default = self._default_choice(opts, index=index)
        self._record_widget(WidgetCall(kind="selectbox", label=label, key=key, options=opts, default=default))
        value = self._resolver.resolve(kind="selectbox", label=label, key=key, options=opts, default=default)
        self._set_widget_value(label, key, value)
        _ = format_func
        return value

    def radio(
        self,
        label: str,
        options: Any,
        index: int = 0,
        format_func: Callable[[Any], str] | None = None,
        key: str | None = None,
        **_: Any,
    ) -> Any:
        opts = _normalize_options(options)
        default = self._default_choice(opts, index=index)
        self._record_widget(WidgetCall(kind="radio", label=label, key=key, options=opts, default=default))
        value = self._resolver.resolve(kind="radio", label=label, key=key, options=opts, default=default)
        self._set_widget_value(label, key, value)
        _ = format_func
        return value

    def multiselect(self, label: str, options: Any, default: Any = None, key: str | None = None, **_: Any) -> list[Any]:
        opts = _normalize_options(options)
        default_value = default if default is not None else []
        self._record_widget(WidgetCall(kind="multiselect", label=label, key=key, options=opts, default=default_value))
        value = self._resolver.resolve(
            kind="multiselect", label=label, key=key, options=opts, default=default_value
        )
        if value is None:
            value = []
        if not isinstance(value, list):
            value = [value]
        self._set_widget_value(label, key, value)
        return value

    def slider(
        self,
        label: str,
        min_value: Any = None,
        max_value: Any = None,
        value: Any = None,
        step: Any = None,
        key: str | None = None,
        **_: Any,
    ) -> Any:
        default_value = value if value is not None else min_value
        self._record_widget(WidgetCall(kind="slider", label=label, key=key, options=None, default=default_value))
        resolved = self._resolver.resolve(kind="slider", label=label, key=key, options=[], default=default_value)
        self._set_widget_value(label, key, resolved)
        _ = (min_value, max_value, step)
        return resolved

    def number_input(
        self,
        label: str,
        min_value: Any = None,
        max_value: Any = None,
        value: Any = None,
        step: Any = None,
        format: str | None = None,
        key: str | None = None,
        **_: Any,
    ) -> Any:
        default_value = value if value is not None else (min_value if min_value is not None else 0)
        self._record_widget(
            WidgetCall(kind="number_input", label=label, key=key, options=None, default=default_value)
        )
        resolved = self._resolver.resolve(
            kind="number_input", label=label, key=key, options=[], default=default_value
        )
        self._set_widget_value(label, key, resolved)
        _ = (min_value, max_value, step, format)
        return resolved

    def text_input(self, label: str, value: str = "", key: str | None = None, **_: Any) -> str:
        self._record_widget(WidgetCall(kind="text_input", label=label, key=key, options=None, default=value))
        resolved = self._resolver.resolve(kind="text_input", label=label, key=key, options=[], default=value)
        self._set_widget_value(label, key, resolved)
        return str(resolved)

    def date_input(self, label: str, value: Any = None, key: str | None = None, **_: Any) -> Any:
        default_value = value
        self._record_widget(WidgetCall(kind="date_input", label=label, key=key, options=None, default=default_value))
        resolved = self._resolver.resolve(kind="date_input", label=label, key=key, options=[], default=default_value)
        self._set_widget_value(label, key, resolved)
        return resolved

    def checkbox(self, label: str, value: bool = False, key: str | None = None, **_: Any) -> bool:
        self._record_widget(WidgetCall(kind="checkbox", label=label, key=key, options=None, default=value))
        resolved = self._resolver.resolve(kind="checkbox", label=label, key=key, options=[], default=value)
        self._set_widget_value(label, key, resolved)
        return bool(resolved)

    def button(self, label: str, key: str | None = None, **_: Any) -> bool:
        default_value = self._press_all_buttons
        self._record_widget(WidgetCall(kind="button", label=label, key=key, options=None, default=default_value))
        resolved = self._resolver.resolve(kind="button", label=label, key=key, options=[], default=default_value)
        self._set_widget_value(label, key, resolved)
        return bool(resolved)

    def download_button(self, *_: Any, **__: Any) -> bool:
        # No downloads in static export.
        return False

    def file_uploader(self, label: str, type: Any = None, accept_multiple_files: bool = False, key: str | None = None, **_: Any) -> Any:
        default_value = None
        opts = _normalize_options(type) if type is not None else None
        self._record_widget(WidgetCall(kind="file_uploader", label=label, key=key, options=opts, default=default_value))
        resolved = self._resolver.resolve(kind="file_uploader", label=label, key=key, options=[], default=default_value)
        self._set_widget_value(label, key, resolved)
        _ = accept_multiple_files
        return resolved

    # ----- Misc Streamlit API surface -----
    def set_page_config(self, **_: Any) -> None:
        return None

    def set_option(self, *_: Any, **__: Any) -> None:
        return None

    def stop(self, **_: Any) -> None:
        raise SystemExit("Streamlit stop() called")

    def rerun(self, **_: Any) -> None:
        # In the real runtime this triggers a script rerun; for static export, stop the current run.
        raise SystemExit("Streamlit rerun() called")

    def experimental_rerun(self, **_: Any) -> None:
        raise SystemExit("Streamlit experimental_rerun() called")

    @property
    def session_state(self) -> "SessionState":
        if not hasattr(self, "_session_state"):
            self._session_state = SessionState()
        return self._session_state

    def cache_data(self, func: Callable[..., Any] | None = None, **_: Any):
        if func is None:
            return lambda f: f
        return func

    def cache_resource(self, func: Callable[..., Any] | None = None, **_: Any):
        if func is None:
            return lambda f: f
        return func


class _Container:
    def __init__(self, capture: StreamlitCapture, *, container_id: str) -> None:
        self._capture = capture
        self._container_id = container_id

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._capture, name)
        if not callable(attr):
            return attr

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            kwargs.setdefault("container", self._container_id)
            return attr(*args, **kwargs)

        return wrapped

    def __enter__(self) -> "_Container":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _Progress:
    def __init__(self, capture: StreamlitCapture) -> None:
        self._capture = capture

    def progress(self, value: Any, **_: Any) -> "_Progress":
        self._capture._capture("text", f"Progress: {value}", title="Progress")
        return self


class SessionState(dict):
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as e:
            raise AttributeError(name) from e

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError as e:
            raise AttributeError(name) from e


def build_fake_streamlit_module(capture: StreamlitCapture) -> types.ModuleType:
    mod = types.ModuleType("streamlit")
    # Mark as "package" so imports like `import streamlit.components.v1` work.
    mod.__path__ = []  # type: ignore[attr-defined]
    for name in dir(capture):
        if name.startswith("_"):
            continue
        setattr(mod, name, getattr(capture, name))
    mod.sidebar = _Container(capture, container_id="sidebar")
    return mod


def build_fake_streamlit_modules(capture: StreamlitCapture) -> dict[str, types.ModuleType]:
    st = build_fake_streamlit_module(capture)

    components_pkg = types.ModuleType("streamlit.components")
    components_pkg.__path__ = []  # type: ignore[attr-defined]

    v1 = types.ModuleType("streamlit.components.v1")

    def declare_component(*_args: Any, **_kwargs: Any):
        def component_func(**__kwargs: Any):
            # Components return values from the frontend; for export, stable zero is fine.
            return 0

        return component_func

    v1.declare_component = declare_component  # type: ignore[attr-defined]

    # Wire attributes so both import styles work.
    components_pkg.v1 = v1  # type: ignore[attr-defined]
    st.components = components_pkg  # type: ignore[attr-defined]

    return {
        "streamlit": st,
        "streamlit.components": components_pkg,
        "streamlit.components.v1": v1,
    }


def run_streamlit_script(
    script_path: Path,
    *,
    overrides: dict[str, Any] | None = None,
    press_all_buttons: bool = False,
    record_widgets: bool = True,
    extra_sys_path: list[Path] | None = None,
    env: dict[str, str] | None = None,
) -> StreamlitCapture:
    capture = StreamlitCapture(overrides=overrides, press_all_buttons=press_all_buttons, record_widgets=record_widgets)
    fake_modules = build_fake_streamlit_modules(capture)

    old_env = os.environ.copy()
    old_sys_path = list(sys.path)
    old_modules = dict(sys.modules)
    try:
        if env:
            os.environ.update(env)
        if extra_sys_path:
            for p in extra_sys_path:
                sys.path.insert(0, str(p))
        for name, mod in fake_modules.items():
            sys.modules[name] = mod
        try:
            runpy.run_path(str(script_path), run_name="__main__")
        except SystemExit:
            # Many Streamlit apps call st.stop()/st.rerun() as control flow.
            pass
        return capture
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        sys.path[:] = old_sys_path
        sys.modules.clear()
        sys.modules.update(old_modules)


def dump_widget_calls(widget_calls: list[WidgetCall]) -> str:
    serializable = [dataclasses.asdict(call) for call in widget_calls]
    return json.dumps(serializable, indent=2, default=str)
