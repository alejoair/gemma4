"""Validate a submission directory with the competition's own libraries.

Usage (needs Python >= 3.12 and the wheels from the Kaggle dataset
`metric/gemma-4-developer-agent-wheelhouse`: swegemma, adk_submission, adk_eval_core):

    python tools/validate_submission.py path/to/submission [--zip out.zip]

Runs the same checks the scoring harness runs before inference:
  1. validate_directory   - symlinks, file count/size, extensions, one root config
  2. single-model rule    - one base model, must be the competition model
  3. compile_submission   - YAML schema, !include, tools/skills/adapters resolve,
                            generation_config constraints
  4. eval_config.yaml     - parses and has sane budgets
  5. optional zip         - same size/extension checks the notebook applies
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from pathlib import Path

import yaml
from adk_submission import ModelRegistry, ToolRegistry, compile_submission
from adk_submission.discovery import discover_adapters, validate_directory
from swegemma.config import (
    ALLOWED_ADAPTER_EXTENSIONS,
    ALLOWED_SUBMISSION_EXTENSIONS,
    MAX_SUBMISSION_SIZE_BYTES,
    build_submission_limits,
)
from swegemma.models.discovery import validate_single_declared_model

COMPETITION_MODEL = 'gemma-4-31b-it-qat-w4a16-ct'
TOOL_NAMES = [
    'run_command', 'submit_patch', 'get_status', 'read_file', 'edit_file',
    'write_file', 'get_code_neighbors', 'search_similar_code', 'get_code_subgraph',
]


def _stub(name: str):
    def tool(*args, **kwargs) -> str:
        return '{"status": "ok"}'

    tool.__name__ = name
    return tool


def check_directory(path: Path):
    limits, constraints = build_submission_limits()
    validate_directory(path, limits)
    print('[ok] directory structure, extensions, size')
    return limits, constraints


def check_model(path: Path) -> str:
    model = validate_single_declared_model(path)
    if model != COMPETITION_MODEL:
        raise SystemExit(f'[FAIL] model is {model!r}, competition requires {COMPETITION_MODEL!r}')
    print(f'[ok] single base model: {model}')
    return model


def check_compile(path: Path, model: str, limits, constraints) -> None:
    adapters = discover_adapters(str(path), adapter_extensions=ALLOWED_ADAPTER_EXTENSIONS)
    models = ModelRegistry()
    models.register(model, f'openai/{model}')
    adapters.register_all(models, lambda info: f'openai/{model}')
    tools = ToolRegistry()
    for name in TOOL_NAMES:
        tools.register(name, _stub(name))
    compile_submission(
        path,
        tool_registry=tools,
        model_registry=models,
        limits=limits,
        generation_constraints=constraints,
        adapter_manifest=adapters,
    )
    print(f'[ok] compiled agent tree (adapters found: {sorted(adapters.adapters)})')


def check_eval_config(path: Path) -> None:
    cfg_file = path / 'eval_config.yaml'
    if not cfg_file.exists():
        print('[ok] no eval_config.yaml (harness defaults apply)')
        return
    section = (yaml.safe_load(cfg_file.read_text(encoding='utf-8')) or {}).get('evaluation', {})
    unknown = set(section) - {'timeout_seconds', 'max_tool_calls', 'max_time_minutes', 'max_turns'}
    if unknown:
        raise SystemExit(f'[FAIL] unknown eval_config keys: {sorted(unknown)}')
    print(f'[ok] eval_config: {section}')


def check_zip(path: Path, out: Path) -> None:
    zip_path = Path(shutil.make_archive(str(out.with_suffix('')), 'zip', root_dir=path))
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        total = sum(i.file_size for i in infos)
        assert total <= MAX_SUBMISSION_SIZE_BYTES, f'archive too large: {total}'
        for i in infos:
            ext = Path(i.filename).suffix.lower()
            assert ext in ALLOWED_SUBMISSION_EXTENSIONS, f'disallowed extension: {i.filename}'
    print(f'[ok] {zip_path} ({zip_path.stat().st_size / 1024:.1f} KiB)')


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('submission', type=Path)
    ap.add_argument('--zip', type=Path, help='also build and check this zip')
    args = ap.parse_args()

    path = args.submission.resolve()
    limits, constraints = check_directory(path)
    model = check_model(path)
    check_compile(path, model, limits, constraints)
    check_eval_config(path)
    if args.zip:
        check_zip(path, args.zip.resolve())
    print('VALID')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # surface the competition's own error text
        print(f'[FAIL] {type(e).__name__}: {e}')
        sys.exit(1)
