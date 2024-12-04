

ROOT="$(realpath "$(dirname "$0")")"

exec "${ROOT}/run.sh" invoke "$@"
