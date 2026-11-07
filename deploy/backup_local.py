"""Create and verify protected local backups for the Windows deployment."""
import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

import pymysql
from dotenv import load_dotenv


BACKUP_VERSION = 1
BACKUP_NAME = re.compile(r"\d{8}T\d{6}Z")
MYSQL_BIN = Path(os.getenv(
    "MYSQL_BIN_DIR", r"C:\Program Files\MySQL\MySQL Server 8.4\bin"))
OBJECTS_QUERY = (
    "SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.tables "
    "WHERE table_schema=%s ORDER BY TABLE_NAME"
)
COLUMNS_QUERY = (
    "SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,COLUMN_TYPE,IS_NULLABLE,"
    "COLUMN_DEFAULT,EXTRA,GENERATION_EXPRESSION FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION"
)
INDEXES_QUERY = (
    "SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,SUB_PART,"
    "INDEX_TYPE,EXPRESSION FROM information_schema.STATISTICS "
    "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX"
)


def local_paths():
    local = Path(os.environ["LOCALAPPDATA"])
    huisuo = local / "Huisuo"
    return {
        "backup_root": huisuo / "backups",
        "state": huisuo / "channel-sync",
        "announcement_media": huisuo / "announcement-media",
        "music_library": Path(os.getenv("MUSIC_LIBRARY_DIR", str(huisuo / "music"))),
    }


def protect_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    identity = subprocess.check_output(["whoami.exe"], text=True).strip()
    result = subprocess.run(
        [
            "icacls.exe", str(path), "/inheritance:r", "/grant:r",
            f"{identity}:(OI)(CI)F", "NT AUTHORITY\\SYSTEM:(OI)(CI)F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Could not restrict backup directory permissions: "
                           + (result.stderr or result.stdout)[-1000:])


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = source.stat()
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, destination.open("wb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(block)
            digest.update(block)
            size += len(block)
        writer.flush()
        os.fsync(writer.fileno())
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Source changed while being copied: {source.name}")
    if size != before.st_size:
        raise RuntimeError(f"Incomplete copy: {source.name}")
    return size, digest.hexdigest()


def copy_tree(source, destination):
    records = {}
    if not source.exists():
        return records
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"Expected a regular directory: {source}")
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            candidate = current_path / name
            if candidate.is_symlink():
                raise RuntimeError(f"Refusing to follow a directory link: {candidate.name}")
        for name in filenames:
            original = current_path / name
            if original.is_symlink() or not original.is_file():
                raise RuntimeError(f"Refusing to copy a non-regular file: {original.name}")
            relative = original.relative_to(source)
            target = destination / relative
            size, digest = copy_file(original, target)
            records[(destination.name + "/" + relative.as_posix())] = {
                "bytes": size,
                "sha256": digest,
            }
    return records


def sqlite_snapshots(source_dir, destination_dir):
    records = {}
    if not source_dir.exists():
        return records
    for source in sorted(source_dir.glob("*.sqlite3")):
        if source.is_symlink() or not source.is_file():
            raise RuntimeError(f"Refusing to copy a non-regular SQLite file: {source.name}")
        destination = destination_dir / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(
                    source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as original:
                original.execute("PRAGMA busy_timeout=30000")
                check = original.execute("PRAGMA quick_check").fetchone()[0]
                if check != "ok":
                    raise RuntimeError(f"SQLite state check failed for {source.name}: {check}")
                with closing(sqlite3.connect(destination, timeout=30)) as snapshot:
                    original.backup(snapshot, pages=256, sleep=0.1)
                    copied_check = snapshot.execute("PRAGMA quick_check").fetchone()[0]
                    if copied_check != "ok":
                        raise RuntimeError(f"SQLite backup check failed for {source.name}: {copied_check}")
                    tables = snapshot.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                    rows = 0
                    for (table,) in tables:
                        escaped = table.replace('"', '""')
                        rows += snapshot.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
            records["state/" + source.name] = {
                "bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
                "tables": len(tables),
                "rows": rows,
            }
        except Exception as error:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f"Could not snapshot SQLite state {source.name}: {error}") from error
    return records


def directory_bytes(source):
    total = 0
    if not source.exists():
        return total
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"Expected a regular directory: {source}")
    for current, directories, filenames in os.walk(source, followlinks=False):
        current_path = Path(current)
        if any((current_path / name).is_symlink() for name in directories):
            raise RuntimeError(f"Unexpected directory link in {source.name}")
        for name in filenames:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"Unexpected non-regular file in {source.name}")
            total += path.stat().st_size
    return total


def database_config():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / "bot" / ".env", override=True)
    host = os.getenv("DB_HOST", "127.0.0.1")
    database = os.getenv("DB_NAME", "venue_db")
    if host.casefold() not in {"localhost", "127.0.0.1", "::1"} or database != "venue_db":
        raise RuntimeError("Safety check failed: expected the local venue_db configured in bot/.env")
    return {
        "host": host,
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": database,
    }


def connect(config, database=None):
    return pymysql.connect(
        host=config["host"],
        port=config["port"],
        user=config["user"],
        password=config["password"],
        database=database or config["database"],
        charset="utf8mb4",
        connect_timeout=15,
        read_timeout=300,
        write_timeout=300,
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def mysql_option_file(path, config):
    def quote(value):
        value = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return '"' + value.replace("\n", "\\n").replace("\r", "\\r") + '"'

    path.write_text(
        "[client]\n"
        f"host={quote(config['host'])}\n"
        f"port={config['port']}\n"
        f"user={quote(config['user'])}\n"
        f"password={quote(config['password'])}\n",
        encoding="utf-8",
    )


def object_metadata(cursor, database):
    cursor.execute(OBJECTS_QUERY, (database,))
    objects = [tuple(row.values()) for row in cursor.fetchall()]
    cursor.execute(COLUMNS_QUERY, (database,))
    columns = [tuple(row.values()) for row in cursor.fetchall()]
    cursor.execute(INDEXES_QUERY, (database,))
    indexes = [tuple(row.values()) for row in cursor.fetchall()]
    return objects, columns, indexes


def metadata_hash(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def run_client(executable, option_file, *arguments, timeout=1800, stdin=None):
    if not executable.is_file():
        raise RuntimeError(f"MySQL client not found: {executable}")
    return subprocess.run(
        [str(executable), f"--defaults-extra-file={option_file}", *arguments],
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )


def backup(args):
    paths = local_paths()
    backup_root = paths["backup_root"]
    protect_directory(backup_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = backup_root / stamp
    staging = backup_root / (stamp + ".partial")
    if final.exists() or staging.exists():
        raise RuntimeError("A backup with this timestamp already exists; retry shortly")
    staging.mkdir()
    config = database_config()
    option_file = staging / ".mysql-client.cnf"
    try:
        with connect(config) as database:
            with database.cursor() as cursor:
                cursor.execute(
                    "SELECT ENGINE FROM information_schema.tables "
                    "WHERE table_schema=%s AND table_type='BASE TABLE'",
                    (config["database"],),
                )
                engines = {row["ENGINE"] for row in cursor.fetchall()}
                if not engines or engines != {"InnoDB"}:
                    raise RuntimeError("Backup requires a non-empty schema containing only InnoDB tables")
                objects, columns, indexes = object_metadata(cursor, config["database"])
                cursor.execute(
                    "SELECT DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME "
                    "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",
                    (config["database"],),
                )
                encoding = cursor.fetchone()
                if not encoding:
                    raise RuntimeError("Configured database does not exist")
                cursor.execute(
                    "SELECT COALESCE(SUM(data_length+index_length),0) AS bytes "
                    "FROM information_schema.tables WHERE table_schema=%s",
                    (config["database"],),
                )
                estimated_database_bytes = cursor.fetchone()["bytes"]

        mysql_option_file(option_file, config)
        expected_bytes = (
            estimated_database_bytes
            + directory_bytes(paths["state"])
            + directory_bytes(paths["announcement_media"])
            + (directory_bytes(paths["music_library"]) if args.include_local_music else 0)
        )
        required_bytes = expected_bytes + max(50 * 1024 * 1024, expected_bytes // 10)
        if shutil.disk_usage(backup_root).free < required_bytes:
            raise RuntimeError(
                f"Insufficient free space: need approximately {required_bytes / (1024 ** 3):.2f} GiB")
        dump = run_client(
            MYSQL_BIN / "mysqldump.exe",
            option_file,
            "--single-transaction", "--quick", "--routines", "--events",
            "--triggers", "--hex-blob", "--no-tablespaces",
            "--column-statistics=0", "--set-gtid-purged=OFF",
            f"--result-file={staging / 'venue_db.sql'}",
            config["database"],
        )
        if dump.returncode:
            raise RuntimeError("mysqldump failed: " + (dump.stderr or dump.stdout)[-2000:])
        sql_path = staging / "venue_db.sql"
        if not sql_path.is_file() or sql_path.stat().st_size < 1024:
            raise RuntimeError("mysqldump produced an empty or unexpectedly small backup")
        with sql_path.open("r", encoding="utf-8", errors="replace") as stream:
            if any(re.match(r"^\s*(?:USE\s+|CREATE\s+DATABASE\b)", line, re.I) for line in stream):
                raise RuntimeError("Dump contains database-switch statements; refusing an unsafe restore package")

        files = {
            "venue_db.sql": {"bytes": sql_path.stat().st_size, "sha256": sha256_file(sql_path)}
        }
        files.update(sqlite_snapshots(paths["state"], staging / "state"))
        media_files = copy_tree(paths["announcement_media"], staging / "announcement-media")
        files.update(media_files)
        if args.include_local_music:
            music_files = copy_tree(paths["music_library"], staging / "music-library")
            files.update(music_files)

        option_file.unlink()
        manifest = {
            "version": BACKUP_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "database": config["database"],
            "mysql_objects": len(objects),
            "mysql_columns_sha256": metadata_hash(columns),
            "mysql_indexes_sha256": metadata_hash(indexes),
            "announcement_media_included": paths["announcement_media"].exists(),
            "local_music_included": bool(args.include_local_music),
            "files": files,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(staging, final)
        prune_backups(backup_root, args.keep_days)
        total_bytes = sum(item["bytes"] for item in files.values())
        print(f"备份完成：{final}")
        print(f"文件 {len(files)} 个，约 {total_bytes / (1024 ** 3):.2f} GiB")
        if not args.include_local_music:
            print("本地音乐目录未包含；如有无法从 Telegram 重建的手工导入音乐，请另行备份。")
        print("建议运行：python deploy/backup_local.py verify")
    except BaseException:
        if option_file.exists():
            option_file.unlink()
        if staging.exists():
            shutil.rmtree(staging)
        raise


def prune_backups(backup_root, keep_days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    for path in backup_root.iterdir():
        if not path.is_dir() or not BACKUP_NAME.fullmatch(path.name):
            continue
        created = datetime.strptime(path.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if created < cutoff:
            shutil.rmtree(path)


def checked_files(package, manifest):
    listed = manifest.get("files")
    if not isinstance(listed, dict) or not listed:
        raise RuntimeError("Backup manifest has no file inventory")
    actual = set()
    for path in package.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Backup contains an unexpected symbolic link")
        if path.is_file() and path.name != "manifest.json":
            actual.add(path.relative_to(package).as_posix())
    if actual != set(listed):
        raise RuntimeError("Backup file inventory does not match its manifest")
    for relative, expected in listed.items():
        rel = PurePosixPath(relative)
        if rel.is_absolute() or ".." in rel.parts or not rel.parts:
            raise RuntimeError("Backup manifest contains an unsafe path")
        path = package.joinpath(*rel.parts)
        if not path.is_file() or path.stat().st_size != expected.get("bytes"):
            raise RuntimeError(f"Backup file size check failed: {relative}")
        if sha256_file(path) != expected.get("sha256"):
            raise RuntimeError(f"Backup checksum failed: {relative}")
    return listed


def restore_verify(package):
    manifest_path = package / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Backup manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != BACKUP_VERSION or manifest.get("database") != "venue_db":
        raise RuntimeError("Unsupported backup version or database")
    files = checked_files(package, manifest)
    if "venue_db.sql" not in files:
        raise RuntimeError("MySQL dump is missing from this backup")
    state_files = [name for name in files if name.startswith("state/") and name.endswith(".sqlite3")]
    for relative in state_files:
        import sqlite3
        with closing(sqlite3.connect(
                (package / Path(*PurePosixPath(relative).parts)).as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError(f"SQLite quick_check failed: {Path(relative).name}")

    config = database_config()
    test_database = "venue_restore_check_" + uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[a-z0-9_]+", test_database):
        raise RuntimeError("Could not generate an isolated verification schema name")
    option_file = package / ".mysql-client.cnf"
    mysql_option_file(option_file, config)
    created = False
    connection = None
    try:
        connection = connect(config)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",
                (test_database,),
            )
            if cursor.fetchone():
                raise RuntimeError("Generated verification schema already exists")
            cursor.execute(
                "SELECT DEFAULT_CHARACTER_SET_NAME,DEFAULT_COLLATION_NAME "
                "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",
                (config["database"],),
            )
            encoding = cursor.fetchone()
            if not encoding or not re.fullmatch(r"[A-Za-z0-9_]+", encoding["DEFAULT_CHARACTER_SET_NAME"]) \
                    or not re.fullmatch(r"[A-Za-z0-9_]+", encoding["DEFAULT_COLLATION_NAME"]):
                raise RuntimeError("Could not validate source database encoding")
            cursor.execute(
                f"CREATE DATABASE `{test_database}` CHARACTER SET "
                f"{encoding['DEFAULT_CHARACTER_SET_NAME']} COLLATE {encoding['DEFAULT_COLLATION_NAME']}")
            created = True
        with (package / "venue_db.sql").open("rb") as dump:
            restored = run_client(
                MYSQL_BIN / "mysql.exe", option_file, "--default-character-set=utf8mb4",
                "--binary-mode=1", test_database, timeout=1800, stdin=dump,
            )
        if restored.returncode:
            raise RuntimeError("Isolated MySQL restore failed: "
                               + (restored.stderr or restored.stdout)[-3000:])
        with connection.cursor() as cursor:
            objects, columns, indexes = object_metadata(cursor, test_database)
            if len(objects) != manifest.get("mysql_objects"):
                raise RuntimeError("Restored MySQL object count differs from the backup manifest")
            if metadata_hash(columns) != manifest.get("mysql_columns_sha256"):
                raise RuntimeError("Restored MySQL column definitions differ from the backup")
            if metadata_hash(indexes) != manifest.get("mysql_indexes_sha256"):
                raise RuntimeError("Restored MySQL indexes differ from the backup")
            row_count = 0
            base_tables = 0
            for name, kind in objects:
                if kind != "BASE TABLE":
                    continue
                if not re.fullmatch(r"[A-Za-z0-9_$]+", name):
                    raise RuntimeError("Unexpected table name in restored database")
                cursor.execute(f"SELECT COUNT(*) AS n FROM `{test_database}`.`{name}`")
                row_count += cursor.fetchone()["n"]
                base_tables += 1
            if not base_tables or not row_count:
                raise RuntimeError("Restored MySQL database contains no application rows")
        print(f"MySQL 恢复验证通过：{len(objects)} 个对象、{base_tables} 张表、{row_count} 行可读")
        print(f"SQLite 状态库验证通过：{len(state_files)} 个文件")
        print(f"文件校验通过：{len(files)} 个文件，SHA-256 匹配")
        print("验证目标是临时库；生产库未被恢复或修改。")
    finally:
        try:
            if connection is not None and created:
                with connection.cursor() as cursor:
                    cursor.execute(f"DROP DATABASE IF EXISTS `{test_database}`")
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) AS n FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",
                        (test_database,),
                    )
                    if cursor.fetchone()["n"]:
                        raise RuntimeError("Temporary verification database could not be removed")
                print("临时恢复库已清理。")
        finally:
            if connection is not None:
                connection.close()
            if option_file.exists():
                option_file.unlink()


def latest_backup(backup_root):
    candidates = [
        path for path in backup_root.iterdir()
        if path.is_dir() and BACKUP_NAME.fullmatch(path.name)
    ] if backup_root.exists() else []
    if not candidates:
        raise RuntimeError(f"No completed backup found in {backup_root}")
    return max(candidates, key=lambda path: path.name)


def main():
    parser = argparse.ArgumentParser(description="手动备份并验证本机 Huisuo 数据")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup", help="创建受限权限的本机备份")
    create.add_argument("--keep-days", type=int, default=14, help="保留天数，默认 14")
    create.add_argument("--include-local-music", action="store_true",
                        help="包含本机音乐目录；可能占用大量空间")
    verify = commands.add_parser("verify", help="校验最新备份并恢复到临时 MySQL 库")
    verify.add_argument("--path", type=Path, help="指定已完成的备份目录，默认最新备份")
    args = parser.parse_args()
    if args.command == "backup":
        if not 1 <= args.keep_days <= 365:
            parser.error("--keep-days must be between 1 and 365")
        backup(args)
    else:
        paths = local_paths()
        restore_verify(args.path or latest_backup(paths["backup_root"]))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, pymysql.MySQLError,
            subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"备份操作失败：{error}", file=sys.stderr)
        sys.exit(1)
