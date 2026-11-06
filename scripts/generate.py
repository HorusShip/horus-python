"""Regenerate the SDK's typed code from the OpenAPI spec.

    python scripts/generate.py                 # from ../openapi/openapi.json
    python scripts/generate.py --spec URL|PATH  # another copy (e.g. the live one)
    python scripts/generate.py --check          # fail if the committed code is stale

Writes:
    src/horus/types/_models.py     pydantic response models (datamodel-code-generator)
    src/horus/types/_events.py     webhook event registry
    src/horus/types/__init__.py    exports
    src/horus/types/params.py      TypedDicts for nested request bodies
    src/horus/resources/*.py       one module per top-level namespace (sync + async)
    src/horus/resources/_index.py  operationId -> client attribute path
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codegen.exports import model_names, render_events, render_types_init  # noqa: E402
from codegen.models import generate_models  # noqa: E402
from codegen.params import ParamTypes  # noqa: E402
from codegen.resources import build_tree, operation_index, render_module, render_package_init  # noqa: E402
from codegen.spec import Spec  # noqa: E402

DEFAULT_SPEC = ROOT.parent / "openapi" / "openapi.json"
GENERATED_DIRS = ("src/horus/types", "src/horus/resources")
HAND_WRITTEN = {"src/horus/resources/_jobs.py", "src/horus/resources/_webhooks.py"}


def load_spec(source: str) -> Spec:
    if source.startswith(("http://", "https://")):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "openapi.json"
            urllib.request.urlretrieve(source, target)  # noqa: S310 - explicit user-provided URL
            return Spec.load(target)
    return Spec.load(Path(source))


def render_all(spec: Spec, out: Path) -> None:
    types_dir, resources_dir = out / "src/horus/types", out / "src/horus/resources"
    types_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)
    generate_models(spec, types_dir / "_models.py")
    names = model_names((types_dir / "_models.py").read_text(encoding="utf-8"))
    (types_dir / "_events.py").write_text(render_events(spec.webhook_events()), encoding="utf-8")
    (types_dir / "__init__.py").write_text(render_types_init(names), encoding="utf-8")
    ops = spec.operations()
    roots = build_tree(ops)
    param_types = ParamTypes(spec)
    for name, root in roots.items():
        (resources_dir / f"{name}.py").write_text(render_module(root, param_types), encoding="utf-8")
    (resources_dir / "__init__.py").write_text(render_package_init(roots), encoding="utf-8")
    (resources_dir / "_index.py").write_text(operation_index(ops), encoding="utf-8")
    (types_dir / "params.py").write_text(param_types.render_module(), encoding="utf-8")
    _format(
        out, [types_dir / "params.py", types_dir / "_events.py", types_dir / "__init__.py", *resources_dir.glob("*.py")]
    )


def _format(out: Path, files: list[Path]) -> None:
    ruff = Path(sys.executable).with_name("ruff")
    targets = [str(f) for f in files if str(f.relative_to(out)).replace("\\", "/") not in HAND_WRITTEN]
    config = str(ROOT / "pyproject.toml")
    subprocess.run(
        [str(ruff), "check", "--config", config, "--select", "I,F401", "--fix", "--quiet", *targets], check=False
    )
    subprocess.run([str(ruff), "format", "--config", config, "--quiet", *targets], check=True)


def snapshot(base: Path) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for d in GENERATED_DIRS:
        for f in sorted((base / d).glob("*.py")):
            rel = str(f.relative_to(base)).replace("\\", "/")
            if rel not in HAND_WRITTEN:
                files[rel] = f.read_text(encoding="utf-8")
    return files


def check(spec: Spec) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        for rel in HAND_WRITTEN:
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, out / rel)
        render_all(spec, out)
        fresh, committed = snapshot(out), snapshot(ROOT)
    stale = sorted(k for k in fresh.keys() | committed.keys() if fresh.get(k) != committed.get(k))
    for name in stale:
        print(f"stale: {name}")
    return 1 if stale else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", default=str(DEFAULT_SPEC), help="OpenAPI JSON path or URL")
    parser.add_argument("--check", action="store_true", help="exit 1 if generated code is out of date")
    args = parser.parse_args()
    spec = load_spec(args.spec)
    if args.check:
        return check(spec)
    render_all(spec, ROOT)
    print(f"generated {len(spec.operations())} operations, {len(spec.webhook_events())} webhook events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
