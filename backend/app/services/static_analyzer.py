"""Stage 1 of the inferrer pipeline — deterministic repository analysis.

Reads the ``upstream/`` subdirectory of a workspace and returns a typed
``StaticFacts`` bundle. No LLM calls; no hallucination risk.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from app.core.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class StaticFacts:
    languages: list[str] = field(default_factory=list)
    python_version: str | None = None
    python_version_source: str | None = None
    node_version: str | None = None
    node_version_source: str | None = None
    package_json_scripts: dict[str, str] = field(default_factory=dict)
    has_dockerfile: bool = False
    has_apptainer_def: bool = False
    has_compose_yaml: bool = False
    detected_env_references: list[str] = field(default_factory=list)
    daemon_indicators: list[str] = field(default_factory=list)
    gpu_libs: list[str] = field(default_factory=list)
    license_keywords: list[str] = field(default_factory=list)
    has_alembic_ini: bool = False
    has_prisma_schema: bool = False
    repo_size_bytes: int = 0
    entry_files: list[str] = field(default_factory=list)
    github_workflows: list[str] = field(default_factory=list)
    readme_run_commands: list[str] = field(default_factory=list)
    commit_sha: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# File enumeration utilities
# ---------------------------------------------------------------------------


# Directories we never descend into when scanning.
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "target",  # rust / java
    ".tox",
    ".gradle",
}

_MAX_SCAN_BYTES_PER_FILE = 256 * 1024  # 256 KB cap when grepping
_MAX_TOTAL_SCAN_FILES = 2000


def _iter_files(root: Path) -> Iterable[Path]:
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            count += 1
            if count > _MAX_TOTAL_SCAN_FILES:
                return
            yield Path(dirpath) / name


def _read_text_safe(p: Path, *, limit: int = _MAX_SCAN_BYTES_PER_FILE) -> str:
    try:
        with p.open("rb") as f:
            data = f.read(limit)
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _file_size_bytes(root: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            full = Path(dirpath) / name
            try:
                total += full.stat().st_size
            except OSError:
                continue
    return total


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


_LANG_EXTENSIONS: dict[str, str] = {
    ".py": "python",
    ".js": "nodejs",
    ".mjs": "nodejs",
    ".cjs": "nodejs",
    ".ts": "nodejs",
    ".tsx": "nodejs",
    ".jsx": "nodejs",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".rb": "ruby",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".cc": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".sh": "shell",
    ".ps1": "powershell",
    ".m": "matlab",
}

_LANG_FILE_HINTS: dict[str, str] = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "tox.ini": "python",
    "package.json": "nodejs",
    "pnpm-lock.yaml": "nodejs",
    "yarn.lock": "nodejs",
    "package-lock.json": "nodejs",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "Gemfile": "ruby",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "kotlin",
}


def detect_languages(workspace: Path) -> list[str]:
    counts: dict[str, int] = {}
    saw_file_hint: set[str] = set()

    for child in workspace.iterdir():
        if child.is_file() and child.name in _LANG_FILE_HINTS:
            saw_file_hint.add(_LANG_FILE_HINTS[child.name])

    for path in _iter_files(workspace):
        # File-name hints (nested too).
        if path.name in _LANG_FILE_HINTS:
            saw_file_hint.add(_LANG_FILE_HINTS[path.name])
        lang = _LANG_EXTENSIONS.get(path.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    # Languages with at least 1 source file OR a strong file hint.
    detected = set(saw_file_hint)
    for lang, n in counts.items():
        if n >= 1:
            detected.add(lang)

    # Order: most-evidence first, with hint-only langs appended.
    ranked = sorted(
        detected,
        key=lambda lg: (-(counts.get(lg, 0)), lg),
    )
    return ranked


# ---------------------------------------------------------------------------
# Python version
# ---------------------------------------------------------------------------


def read_python_version(workspace: Path) -> tuple[str | None, str | None]:
    candidates: list[tuple[Path, str]] = [
        (workspace / ".python-version", ".python-version"),
        (workspace / "runtime.txt", "runtime.txt"),
        (workspace / ".tool-versions", ".tool-versions"),
        (workspace / "pyproject.toml", "pyproject.toml"),
        (workspace / "Pipfile", "Pipfile"),
        (workspace / "setup.py", "setup.py"),
        (workspace / "tox.ini", "tox.ini"),
    ]
    for path, label in candidates:
        if not path.exists():
            continue
        text = _read_text_safe(path)
        version = _parse_python_version(label, text)
        if version:
            return version, label
    return None, None


_PY_VER_RE = re.compile(r"3\.(\d{1,2})(?:\.\d+)?")


def _parse_python_version(label: str, text: str) -> str | None:
    if label == ".python-version":
        m = _PY_VER_RE.search(text)
        return f"3.{m.group(1)}" if m else None
    if label == "runtime.txt":
        # e.g. "python-3.11.7"
        m = re.search(r"python-(3)\.(\d{1,2})", text)
        return f"3.{m.group(2)}" if m else None
    if label == ".tool-versions":
        m = re.search(r"^\s*python\s+3\.(\d{1,2})", text, re.MULTILINE)
        return f"3.{m.group(1)}" if m else None
    if label == "pyproject.toml":
        # requires-python = ">=3.10,<3.12" — pick the lower bound.
        m = re.search(r"requires-python\s*=\s*[\"']([^\"']+)[\"']", text)
        if not m:
            return None
        spec = m.group(1)
        v = _PY_VER_RE.search(spec)
        return f"3.{v.group(1)}" if v else None
    if label == "Pipfile":
        m = re.search(r"python_version\s*=\s*[\"']3\.(\d{1,2})[\"']", text)
        return f"3.{m.group(1)}" if m else None
    if label == "setup.py":
        m = re.search(r"python_requires\s*=\s*[\"']([^\"']+)[\"']", text)
        if not m:
            return None
        v = _PY_VER_RE.search(m.group(1))
        return f"3.{v.group(1)}" if v else None
    if label == "tox.ini":
        m = re.search(r"py3(\d{1,2})", text)
        return f"3.{m.group(1)}" if m else None
    return None


# ---------------------------------------------------------------------------
# Node version
# ---------------------------------------------------------------------------


def read_node_version(workspace: Path) -> tuple[str | None, str | None]:
    nvmrc = workspace / ".nvmrc"
    if nvmrc.exists():
        text = _read_text_safe(nvmrc).strip()
        m = re.search(r"v?(\d{1,2})(?:\.\d+){0,2}", text)
        if m:
            return m.group(1), ".nvmrc"

    pkg = workspace / "package.json"
    if pkg.exists():
        try:
            data = json.loads(_read_text_safe(pkg))
            engines = data.get("engines") if isinstance(data, dict) else None
            if isinstance(engines, dict):
                node = engines.get("node")
                if isinstance(node, str):
                    m = re.search(r"(\d{1,2})", node)
                    if m:
                        return m.group(1), "package.json#engines.node"
        except Exception:
            pass

    tv = workspace / ".tool-versions"
    if tv.exists():
        m = re.search(r"^\s*nodejs\s+(\d{1,2})", _read_text_safe(tv), re.MULTILINE)
        if m:
            return m.group(1), ".tool-versions"
    return None, None


# ---------------------------------------------------------------------------
# package.json scripts
# ---------------------------------------------------------------------------


def read_package_json_scripts(workspace: Path) -> dict[str, str]:
    pkg = workspace / "package.json"
    if not pkg.exists():
        return {}
    try:
        data = json.loads(_read_text_safe(pkg))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {str(k): str(v) for k, v in scripts.items() if isinstance(v, str)}


# ---------------------------------------------------------------------------
# Env references
# ---------------------------------------------------------------------------


_ENV_PY_PATTERNS = [
    re.compile(r"os\.environ\s*\.\s*get\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
    re.compile(r"os\.environ\[\s*[\"']([A-Z][A-Z0-9_]+)[\"']\]"),
    re.compile(r"os\.getenv\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']"),
]
_ENV_JS_PATTERNS = [
    re.compile(r"process\.env\.([A-Z][A-Z0-9_]+)"),
    re.compile(r"process\.env\[\s*[\"']([A-Z][A-Z0-9_]+)[\"']\s*\]"),
]
_ENV_DOTENV_LINE = re.compile(r"^([A-Z][A-Z0-9_]+)\s*=", re.MULTILINE)


def extract_env_references(workspace: Path, *, cap: int = 50) -> list[str]:
    found: set[str] = set()
    # Code files (py, js, ts, jsx, tsx)
    for path in _iter_files(workspace):
        if path.suffix.lower() in {".py"}:
            text = _read_text_safe(path)
            for pat in _ENV_PY_PATTERNS:
                for m in pat.finditer(text):
                    found.add(m.group(1))
        elif path.suffix.lower() in {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}:
            text = _read_text_safe(path)
            for pat in _ENV_JS_PATTERNS:
                for m in pat.finditer(text):
                    found.add(m.group(1))
        elif path.name in {".env.example", ".env.sample", ".env.template", "env.example"}:
            text = _read_text_safe(path)
            for m in _ENV_DOTENV_LINE.finditer(text):
                found.add(m.group(1))
        if len(found) >= cap * 2:
            break

    return sorted(found)[:cap]


# ---------------------------------------------------------------------------
# Daemon / GPU / License / file presence
# ---------------------------------------------------------------------------


_DAEMON_KEYWORDS = [
    "uvicorn",
    "gunicorn",
    "streamlit run",
    "jupyter",
    "flask run",
    "next dev",
    "next start",
    "vite preview",
    "vite dev",
    "serve",
    "fastapi",
    "hypercorn",
    "daphne",
    "nodemon",
]
_GPU_KEYWORDS = [
    "torch",
    "tensorflow-gpu",
    "jax[cuda]",
    "jaxlib",
    "cupy",
    "tensorrt",
    "onnxruntime-gpu",
    "nvidia/cuda",
    "cuda-toolkit",
]
_LICENSE_KEYWORDS = [
    "lsdyna",
    "ls-dyna",
    "ansys",
    "abaqus",
    "matlab",
    "lmstat",
    "LSTC_LICENSE_SERVER",
    "RLM_LICENSE",
    "LM_LICENSE_FILE",
    "flexlm",
]


def detect_daemon_pattern(workspace: Path) -> list[str]:
    found: set[str] = set()
    # Targets: Dockerfile, package.json scripts, shell scripts, run.sh-ish files.
    targets: list[Path] = []
    for candidate in (
        workspace / "Dockerfile",
        workspace / "docker-compose.yml",
        workspace / "docker-compose.yaml",
        workspace / "package.json",
        workspace / "Procfile",
        workspace / "Makefile",
        workspace / "run.sh",
        workspace / "start.sh",
    ):
        if candidate.exists():
            targets.append(candidate)
    for path in _iter_files(workspace):
        if path.suffix.lower() in {".sh", ".bash"}:
            targets.append(path)

    for path in targets[:50]:
        text = _read_text_safe(path).lower()
        for kw in _DAEMON_KEYWORDS:
            if kw in text:
                found.add(kw)
    return sorted(found)


def needs_gpu(workspace: Path) -> list[str]:
    found: set[str] = set()
    for candidate_name in ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py"):
        path = workspace / candidate_name
        if not path.exists():
            continue
        text = _read_text_safe(path).lower()
        for kw in _GPU_KEYWORDS:
            if kw.lower() in text:
                found.add(kw)
    df = workspace / "Dockerfile"
    if df.exists():
        text = _read_text_safe(df).lower()
        for kw in _GPU_KEYWORDS:
            if kw.lower() in text:
                found.add(kw)
    return sorted(found)


def license_keywords(workspace: Path) -> list[str]:
    found: set[str] = set()
    targets: list[Path] = [
        workspace / "README.md",
        workspace / "README.rst",
        workspace / "Dockerfile",
        workspace / "Apptainer.def",
        workspace / "Makefile",
    ]
    for path in _iter_files(workspace):
        if path.suffix.lower() in {".sh", ".bash", ".py", ".yaml", ".yml"}:
            targets.append(path)
        if len(targets) > 80:
            break
    for path in targets:
        if not path.exists():
            continue
        text = _read_text_safe(path)
        lowered = text.lower()
        for kw in _LICENSE_KEYWORDS:
            if kw.lower() in lowered:
                found.add(kw)
    return sorted(found)


# ---------------------------------------------------------------------------
# README run commands
# ---------------------------------------------------------------------------


_README_HEADING_RE = re.compile(
    r"^#{1,6}\s+(.*?(?:how to run|usage|quickstart|getting started|run|installation|install)).*?$",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)


def extract_readme_commands(workspace: Path, *, cap: int = 20) -> list[str]:
    readme = None
    for name in ("README.md", "README.rst", "README", "Readme.md", "readme.md"):
        candidate = workspace / name
        if candidate.exists():
            readme = candidate
            break
    if readme is None:
        return []

    text = _read_text_safe(readme, limit=128 * 1024)
    if not text:
        return []

    # Find each "run/usage/quickstart" section and extract code blocks under it.
    headings = list(_README_HEADING_RE.finditer(text))
    if not headings:
        # Fall back: grab the first code fence containing a known runner keyword.
        commands: list[str] = []
        for m in _CODE_FENCE_RE.finditer(text):
            block = m.group(1)
            for line in block.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if any(
                    tok in line.lower()
                    for tok in ("python", "pip ", "npm ", "pnpm ", "yarn ", "uvicorn", "streamlit", "docker", "./run", "make ")
                ):
                    commands.append(line)
                if len(commands) >= cap:
                    return commands
        return commands

    commands: list[str] = []
    for i, h in enumerate(headings):
        start = h.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
        section = text[start:end]
        for m in _CODE_FENCE_RE.finditer(section):
            for line in m.group(1).splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("$ "):
                    line = line[2:]
                commands.append(line)
                if len(commands) >= cap:
                    return commands
    return commands


# ---------------------------------------------------------------------------
# Entry files, workflows, commit sha
# ---------------------------------------------------------------------------


_ENTRY_FILE_HINTS = [
    "app/main.py",
    "src/main.py",
    "main.py",
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "src/index.js",
    "src/index.ts",
    "src/server.ts",
    "src/server.js",
    "server.js",
    "index.js",
    "app.py",
    "bin/run",
]


def detect_entry_files(workspace: Path) -> list[str]:
    found = []
    for rel in _ENTRY_FILE_HINTS:
        if (workspace / rel).is_file():
            found.append(rel)
    return found


def list_github_workflows(workspace: Path) -> list[str]:
    wf_dir = workspace / ".github" / "workflows"
    if not wf_dir.exists():
        return []
    return sorted(
        f.name
        for f in wf_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".yml", ".yaml"}
    )


def read_commit_sha(workspace: Path) -> str | None:
    """Read upstream/.git/HEAD when present; resolves refs/heads/* if needed."""
    head_file = workspace / ".git" / "HEAD"
    if not head_file.exists():
        return None
    try:
        content = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if content.startswith("ref: "):
        ref = content[5:].strip()
        ref_path = workspace / ".git" / ref
        if ref_path.exists():
            try:
                return ref_path.read_text(encoding="utf-8").strip() or None
            except OSError:
                return None
        # Packed refs fallback.
        packed = workspace / ".git" / "packed-refs"
        if packed.exists():
            try:
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line.endswith(" " + ref):
                        return line.split(" ", 1)[0].strip() or None
            except OSError:
                return None
        return None
    return content or None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def analyze(workspace: Path) -> StaticFacts:
    """Analyze ``workspace/upstream``. ``workspace`` is ``app_workspaces/{id}``."""
    if workspace.name == "upstream":
        upstream = workspace
    else:
        upstream = workspace / "upstream"
    if not upstream.exists():
        logger.warning("static_analyzer.analyze: upstream missing at %s", upstream)
        return StaticFacts()

    facts = StaticFacts()
    facts.languages = detect_languages(upstream)
    facts.python_version, facts.python_version_source = read_python_version(upstream)
    facts.node_version, facts.node_version_source = read_node_version(upstream)
    facts.package_json_scripts = read_package_json_scripts(upstream)

    facts.has_dockerfile = (upstream / "Dockerfile").exists()
    facts.has_apptainer_def = any(
        (upstream / n).exists() for n in ("Apptainer.def", "Singularity", "Singularity.def")
    )
    facts.has_compose_yaml = any(
        (upstream / n).exists() for n in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml", "compose.yml")
    )

    facts.detected_env_references = extract_env_references(upstream)
    facts.daemon_indicators = detect_daemon_pattern(upstream)
    facts.gpu_libs = needs_gpu(upstream)
    facts.license_keywords = license_keywords(upstream)
    facts.has_alembic_ini = (upstream / "alembic.ini").exists()
    facts.has_prisma_schema = (upstream / "prisma" / "schema.prisma").exists() or (
        upstream / "schema.prisma"
    ).exists()
    facts.repo_size_bytes = _file_size_bytes(upstream)
    facts.entry_files = detect_entry_files(upstream)
    facts.github_workflows = list_github_workflows(upstream)
    facts.readme_run_commands = extract_readme_commands(upstream)
    facts.commit_sha = read_commit_sha(upstream)
    return facts
