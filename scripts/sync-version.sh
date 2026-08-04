#!/usr/bin/env bash
# 版本同步脚本：将仓库根 _version.py 中的版本号同步到 addon 各版本字段。
#
# 用法:
#   scripts/sync-version.sh                 全量同步（版本号从 _version.py 读取）
#   scripts/sync-version.sh --addon-only <ver>  仅同步 addon 侧打版（Python 侧不动）
#   scripts/sync-version.sh --check         只校验各文件版本是否一致，不改写
#
# 同步对象:
#   - MCBE-AI-Agent-addon/package.json 的 version（字符串）
#   - behavior/resource manifest 的 header.version、modules[*].version（数组）
#   - behavior manifest dependencies 中 resource pack uuid 引用的 version（数组）
#
# 禁止改动:
#   - min_engine_version
#   - dependencies 中 module_name 型依赖版本（@minecraft/server 等）
#   - 命令清单（真源是 config/settings.py）
#
# 实现说明：写入采用文本级替换，只替换版本号数字，保留文件其余字节不变，
# 保证每次运行后 `git diff` 仅出现版本号相关字段变更。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADDON_DIR="$ROOT_DIR/MCBE-AI-Agent-addon"
PACKAGE_JSON="$ADDON_DIR/package.json"
BEHAVIOR_MANIFEST="$ADDON_DIR/behavior_packs/MCBE-AI-Agent/manifest.json"
RESOURCE_MANIFEST="$ADDON_DIR/resource_packs/MCBE-AI-Agent/manifest.json"
# behavior pack dependencies 中引用的 resource pack uuid
RESOURCE_UUID="450332ea-6755-48b6-ab7f-36843065edd1"

MODE="sync"
ADDON_ONLY_VERSION=""

usage() {
  echo "用法: $0 [--check] [--addon-only <ver>]"
  echo "  --check            只校验各文件版本是否一致，不改写"
  echo "  --addon-only <ver> 仅同步 addon 侧版本（Python 侧不动）"
  echo "  无参数             全量同步（版本号从 _version.py 读取）"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      if [[ "$MODE" == "addon-only" ]]; then
        echo "错误: --check 与 --addon-only 不能同时使用" >&2
        exit 1
      fi
      MODE="check"
      shift
      ;;
    --addon-only)
      if [[ "$MODE" == "check" ]]; then
        echo "错误: --check 与 --addon-only 不能同时使用" >&2
        exit 1
      fi
      MODE="addon-only"
      if [[ $# -lt 2 ]]; then
        echo "错误: --addon-only 需要版本号参数" >&2
        usage
        exit 1
      fi
      ADDON_ONLY_VERSION="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage
      exit 1
      ;;
  esac
done

# 版本格式校验（严格 SemVer：MAJOR.MINOR.PATCH，无前导零）
valid_semver() {
  [[ "$1" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]
}

# 确定目标版本
if [[ "$MODE" == "addon-only" ]]; then
  VERSION="$ADDON_ONLY_VERSION"
  if ! valid_semver "$VERSION"; then
    echo "错误: --addon-only 需要有效的 semver 版本号（如 2.5.1）" >&2
    exit 1
  fi
else
  VERSION="$(cd "$ROOT_DIR" && "${PYTHON_BIN:-python}" -c "from _version import __version__; print(__version__)")"
  if ! valid_semver "$VERSION"; then
    echo "错误: _version.py 中的版本号格式无效: $VERSION（应为 MAJOR.MINOR.PATCH）" >&2
    exit 1
  fi
fi

echo "[sync-version] 目标版本: $VERSION (模式: $MODE)"

# JSON 读取 / 校验 / 写入统一走 Python；写入用文本级替换保持格式
PYTHON_BIN="${PYTHON_BIN:-python}"
"$PYTHON_BIN" - "$MODE" "$VERSION" "$PACKAGE_JSON" "$BEHAVIOR_MANIFEST" "$RESOURCE_MANIFEST" "$RESOURCE_UUID" <<'PYEOF'
import json
import re
import sys

mode, version, package_json, behavior_manifest, resource_manifest, resource_uuid = sys.argv[1:]


def to_array(v):
    return [int(x) for x in v.split(".")]


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def format_array(arr, original):
    """按原文格式输出数组：紧凑原文→紧凑输出，多行原文→保持缩进的多行输出。"""
    if "\n" not in original:
        return json.dumps(arr)
    lines = original.split("\n")
    item_indent = lines[1][: len(lines[1]) - len(lines[1].lstrip())]
    close_indent = lines[-1][: len(lines[-1]) - len(lines[-1].lstrip())]
    parts = ["["]
    for i, n in enumerate(arr):
        parts.append(item_indent + str(n) + ("," if i < len(arr) - 1 else ""))
    parts.append(close_indent + "]")
    return "\n".join(parts)


def replace_in_text(text, new_arr, kind):
    """文本级替换版本字段。kind='array' 只匹配 "version": [...]（不含嵌套数组）；
    kind='str' 匹配 package.json 顶层 "version": "..."。返回 (新文本, 替换次数)。"""
    count = 0
    if kind == "array":
        pattern = re.compile(r'("version"\s*:\s*)(\[[^\[\]]*\])', re.DOTALL)

        def repl(m):
            nonlocal count
            count += 1
            return m.group(1) + format_array(new_arr, m.group(2))

        return pattern.sub(repl, text), count
    else:
        pattern = re.compile(r'("version"\s*:\s*)"[^"]*"')

        def repl(m):
            nonlocal count
            count += 1
            return m.group(1) + json.dumps(version)

        return pattern.sub(repl, text), count


def check_equal(path, actual, expected, desc, problems):
    if actual != expected:
        problems.append(f"{path} {desc} = {actual} (应为 {expected})")


problems = []
expected_str = version
expected_arr = to_array(version)

pkg = read_json(package_json)
check_equal(package_json, pkg.get("version"), expected_str, "version", problems)

bh = read_json(behavior_manifest)
check_equal(behavior_manifest, bh["header"].get("version"), expected_arr, "header.version", problems)
for i, mod in enumerate(bh.get("modules", [])):
    check_equal(behavior_manifest, mod.get("version"), expected_arr, f"modules[{i}].version", problems)
rp_deps = [d for d in bh.get("dependencies", []) if d.get("uuid") == resource_uuid]
if not rp_deps:
    problems.append(f"{behavior_manifest} dependencies 中缺少 resource pack 依赖（uuid={resource_uuid}）")
for i, dep in enumerate(rp_deps):
    check_equal(behavior_manifest, dep.get("version"), expected_arr, "dependencies 中 resource pack 依赖 version", problems)

rs = read_json(resource_manifest)
check_equal(resource_manifest, rs["header"].get("version"), expected_arr, "header.version", problems)
for i, mod in enumerate(rs.get("modules", [])):
    check_equal(resource_manifest, mod.get("version"), expected_arr, f"modules[{i}].version", problems)

if mode == "check":
    if problems:
        for p in problems:
            print(f"不一致: {p}")
        print("[sync-version] 存在不一致，运行全量 sync 修复。")
        sys.exit(1)
    print(f"[sync-version] OK: 所有版本字段一致 ({version})")
    sys.exit(0)

# sync / addon-only 模式：文本级替换（先回滚语义不一致的文件到期望值）
targets = [
    (package_json, "str"),
    (behavior_manifest, "array"),
    (resource_manifest, "array"),
]
for path, kind in targets:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new_text, count = replace_in_text(text, expected_arr, kind)
    # 语义校验：替换后必须无问题残留
    reloaded = json.loads(new_text)
    if kind == "str":
        ok_semantic = reloaded.get("version") == expected_str
    else:
        ok_semantic = (
            reloaded["header"].get("version") == expected_arr
            and all(m.get("version") == expected_arr for m in reloaded.get("modules", []))
        )
        # 仅 behavior manifest 要求 resource pack 依赖存在且版本一致
        if path == behavior_manifest:
            rp_deps_after = [
                d for d in reloaded.get("dependencies", []) if d.get("uuid") == resource_uuid
            ]
            ok_semantic = ok_semantic and bool(rp_deps_after) and all(
                d.get("version") == expected_arr for d in rp_deps_after
            )
    if not ok_semantic:
        print(f"错误: {path} 文本替换后语义校验失败", file=sys.stderr)
        sys.exit(1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"[sync-version] 已同步 {path} ({count} 处版本字段)")
PYEOF
