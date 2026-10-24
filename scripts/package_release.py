"""Сборка воспроизводимого ZIP только из проверенных исходников по списку."""

import sys
import hashlib
import tomllib
import zipfile

from check_public import ROOT, check


def main() -> None:
    """Собрать только разрешённые исходники; версия берётся из одного pyproject.toml."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    files = check()
    folder = ROOT / "artifacts"
    folder.mkdir(exist_ok=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    output = folder / ("sqlite-event-queue-" + version + ".zip")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            # Фиксируем порядок (из allowlist), время и права: локальные даты файлов
            # не меняют ZIP. Между версиями zlib байты сжатия могут отличаться.
            info = zipfile.ZipInfo("sqlite-event-queue/" + path.relative_to(ROOT).as_posix(), (2026, 10, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Проверка целостности ZIP релиза не пройдена.")
    # Контрольная сумма относится к ZIP снаружи. Её нельзя включить в сам этот ZIP
    # без циклической зависимости «изменил checksum → изменил ZIP → изменил checksum».
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    (folder / "SHA256SUMS").write_text(digest + "  " + output.name + "\n", encoding="utf-8")
    print("Собран релиз с исходниками: " + output.name)


if __name__ == "__main__":
    main()
