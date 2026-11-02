#!/usr/bin/env python3
"""Check, package and describe the skills in this repository.

Subcommands:
  check [--tag TAG]     validate every skills/<name>/SKILL.md against the Agent
                        Skills format, every relative link inside a skill, the
                        JSON manifests and one shared version across them; with
                        --tag also require TAG == v<version>
  package [--out DIR]   write DIR/<skill>.zip per skill (the skill folder at the
                        archive root, as claude.ai expects) and DIR/SHA256SUMS;
                        default DIR is dist

Reads skills/, plugin.json, .claude-plugin/*.json and
.agents/plugins/marketplace.json relative to the repository root, wherever it
is run from.

Exit codes: 0 success, 1 runtime failure, 2 usage error, 3 check found
violations (each printed as one line).

Examples:
  python3 scripts/release.py check
  python3 scripts/release.py check --tag v0.1.0 && python3 scripts/release.py package
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROG = "release.py"
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
MANIFESTS = ("plugin.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json")
EPOCH = (2026, 1, 1, 0, 0, 0)


def skills() -> list[Path]:
    return sorted(path.parent for path in (ROOT / "skills").glob("*/SKILL.md"))


def frontmatter(text: str) -> dict[str, str] | None:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if line and not line[0].isspace() and ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return None


def check_skill(skill: Path) -> list[str]:
    rel = skill.relative_to(ROOT).as_posix()
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    meta = frontmatter(text)
    if meta is None:
        return [f"{rel}/SKILL.md: no frontmatter block between --- lines"]
    problems = []
    name, description = meta.get("name", ""), meta.get("description", "")
    if name != skill.name:
        problems.append(f"{rel}/SKILL.md: name '{name}' must equal the folder name '{skill.name}'")
    if not NAME.match(name) or len(name) > 64:
        problems.append(f"{rel}/SKILL.md: name must be 1-64 lowercase letters, digits and single hyphens")
    if not description or len(description) > 1024:
        problems.append(f"{rel}/SKILL.md: description must be 1-1024 characters, has {len(description)}")
    for key in sorted(meta.keys() - FIELDS):
        problems.append(f"{rel}/SKILL.md: field '{key}' is not in the Agent Skills format")
    for doc in sorted(skill.rglob("*.md")):
        for target in LINK.findall(doc.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (doc.parent / target).resolve().is_file():
                problems.append(f"{doc.relative_to(ROOT).as_posix()}: link '{target}' points to no file")
    return problems


def versions() -> tuple[dict[str, str], list[str]]:
    found, problems = {}, []
    for manifest in MANIFESTS:
        path = ROOT / manifest
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"{manifest}: invalid JSON ({exc})")
            continue
        if "version" in data and manifest != ".claude-plugin/marketplace.json":
            found[manifest] = data["version"]
        for entry in data.get("plugins", []):
            if "version" in entry:
                found[f"{manifest} plugins[{entry.get('name')}]"] = entry["version"]
    return found, problems


def cmd_check(tag: str | None) -> int:
    found = skills()
    problems = [] if found else ["skills/: no skills/<name>/SKILL.md found"]
    for skill in found:
        problems += check_skill(skill)
    seen, manifest_problems = versions()
    problems += manifest_problems
    distinct = sorted(set(seen.values()))
    if len(distinct) != 1:
        problems += [f"{where}: version {value}" for where, value in sorted(seen.items())]
        problems.append("manifests: expected one shared version")
    elif tag is not None and tag != f"v{distinct[0]}":
        problems.append(f"tag {tag} does not match manifest version v{distinct[0]}")
    for problem in problems:
        print(problem)
    if problems:
        print(f"{PROG}: {len(problems)} violation(s)", file=sys.stderr)
        return 3
    print(f"ok: {len(found)} skill(s), version {distinct[0]}")
    return 0


def cmd_package(out: str) -> int:
    found = skills()
    if not found:
        print(f"{PROG}: error: no skills/<name>/SKILL.md found; run 'release.py check'", file=sys.stderr)
        return 1
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sums = []
    for skill in found:
        archive = out_dir / f"{skill.name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(skill.rglob("*")):
                if path.is_file() and path.name != ".DS_Store":
                    info = zipfile.ZipInfo(path.relative_to(skill.parent).as_posix(), EPOCH)
                    info.external_attr = 0o644 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(info, path.read_bytes())
        sums.append(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}")
        print(f"wrote {archive}")
    (out_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    print(f"wrote {out_dir / 'SHA256SUMS'}: {len(sums)} archive(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate skills, links and manifests")
    check.add_argument("--tag", help="release tag that must equal v<version>")
    package = sub.add_parser("package", help="build one ZIP per skill plus SHA256SUMS")
    package.add_argument("--out", default="dist", help="output directory (default: dist)")
    args = parser.parse_args()
    if args.command == "check":
        return cmd_check(args.tag)
    return cmd_package(args.out)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
