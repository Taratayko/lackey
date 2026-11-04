#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="commune_mesh_lackey"
ARCHIVE_NAME="${PROJECT_NAME}_$(date +%Y%m%d_%H%M%S).zip"
TMP_DIR="build_tmp"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT_DIR"

# Проверяет, что новый корневой файл или папка явно добавлены в build.sh.
is_known_root_item() {
  case "$1" in
    adapters|app|application|docs|domain|ports|presentation|tests|scripts|deploy) return 0 ;;
    .env.example|.gitignore|README.md|LICENSE|requirements.txt|start_bot.bat|build.sh) return 0 ;;
    banner.py|bot.py|config.py|database.py|handlers.py|site_feedback_sync.py) return 0 ;;
    *) return 1 ;;
  esac
}

# Игнорирует локальные, секретные и временные элементы, которые не должны попадать в архив.
is_ignored_root_item() {
  case "$1" in
    .|..|.git|.idea|.vscode|.venv|__pycache__|build_tmp|dist|tmp|temp) return 0 ;;
    .env|.env.local|.env.dev|.env.prod|.env.test) return 0 ;;
    .DS_Store|Thumbs.db|.pytest_cache|.mypy_cache|.ruff_cache) return 0 ;;
    *.zip|*.tar.gz|*.log) return 0 ;;
    *) return 1 ;;
  esac
}

# Останавливает сборку, если появился новый корневой элемент без решения: включать или исключать.
check_root_manifest() {
  local unknown=()
  shopt -s dotglob nullglob

  for item in * .*; do
    local name
    name="$(basename "$item")"

    if is_known_root_item "$name" || is_ignored_root_item "$name"; then
      continue
    fi

    unknown+=("$name")
  done

  shopt -u dotglob nullglob

  if (( ${#unknown[@]} > 0 )); then
    echo "Found new root items missing in build.sh:" >&2
    printf ' - %s\n' "${unknown[@]}" >&2
    echo "Decide whether to include them or add them to ignored items." >&2
    exit 2
  fi
}

# Копирует исходники проекта без .env, .venv, IDE-папок и временных данных.
copy_project_files() {
  rm -rf "$TMP_DIR"
  mkdir -p "$TMP_DIR"

  for dir in adapters app application docs domain ports presentation tests scripts deploy; do
    if [[ -d "$dir" ]]; then
      cp -R "$dir" "$TMP_DIR/$dir"
    fi
  done

  for file in .env.example .gitignore README.md LICENSE requirements.txt start_bot.bat build.sh; do
    if [[ -f "$file" ]]; then
      cp "$file" "$TMP_DIR/$file"
    fi
  done

  for file in banner.py bot.py config.py database.py handlers.py site_feedback_sync.py; do
    if [[ -f "$file" ]]; then
      cp "$file" "$TMP_DIR/$file"
    fi
  done
}

# Удаляет Python/IDE/cache мусор, если он случайно попал внутрь копируемых папок.
cleanup_payload() {
  find "$TMP_DIR" -type d \( \
    -name '__pycache__' -o \
    -name '.pytest_cache' -o \
    -name '.mypy_cache' -o \
    -name '.ruff_cache' \
  \) -prune -exec rm -rf {} +

  find "$TMP_DIR" -type f \( \
    -name '*.pyc' -o \
    -name '*.pyo' -o \
    -name '*.log' \
  \) -delete
}

# Проверяет, что секреты и локальные зависимости не попали в build_tmp.
check_archive_payload() {
  if [[ -e "$TMP_DIR/.env" ]]; then
    echo "Error: .env must not be included in archive." >&2
    exit 3
  fi

  if [[ -d "$TMP_DIR/.venv" ]]; then
    echo "Error: .venv must not be included in archive." >&2
    exit 3
  fi

  if [[ -d "$TMP_DIR/.idea" ]]; then
    echo "Error: .idea must not be included in archive." >&2
    exit 3
  fi
}

# Создаёт zip-архив через PowerShell на Windows или через zip на Linux/macOS.
pack_archive() {
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "
      \$ErrorActionPreference = 'Stop';
      Compress-Archive -Path '$TMP_DIR/*' -DestinationPath '$ARCHIVE_NAME' -Force;
    "
  else
    (cd "$TMP_DIR" && zip -qr "../$ARCHIVE_NAME" .)
  fi
}

# Основная сборка архива проекта.
main() {
  echo "Creating archive: $ARCHIVE_NAME"

  check_root_manifest
  copy_project_files
  cleanup_payload
  check_archive_payload
  pack_archive

  rm -rf "$TMP_DIR"
  echo "Done: $ARCHIVE_NAME"
}

main "$@"
