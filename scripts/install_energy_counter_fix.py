"""Install only the energy-counter fix, preserving local changes and IDs."""

import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from urllib.request import urlopen

# Pinned GALEVO R2 parsing/Recorder recovery and HA-native diagnostics download.
# This script can be run without flags: python3 /tmp/install_energy_counter_fix.py
PINNED_REF = "e066bcab3dc72a521fb57d596d6539a325966b00"


BASE_HASHES = {
    "__init__.py": "1295e3d1750651d5f942924a42de9237ea589b48d1e125496da1fc9d2d0f34da",
    "sensor.py": "308de546d8460969b0083bbf9f86388abde403b6f8ef9a3147553b891a7efb20",
}
PRIOR_PATCH_HASHES = {'__init__.py': 'ad8ffb0b608287c328c6bf76f286f1a35736f496782faf7e538eb04a82fbbe89', 'sensor.py': 'c19ac2472e1ae57bf9e9018a36923922c0f82680f776d0eeaedd0e86cc177da5', 'energy_counter.py': '3d5c79aaa8b6b04315d93cd2a49071f71c7e7da3ab9550d0639d6648c6d02080'}
PRIOR_LEGACY_INIT_HASH = '7dc0694632a7c310e2007fb7d455b48772489fbb7860bf7ef44875d66894707b'
# Accept the exact Git blobs that were deployed from PR #5 immediately
# before the boundary fix; reject any other local customizations.
PRIOR_LIVE_BLOB_SHA1 = {
    "__init__.py": {
        "ebe0c04faff84c4f5a196f1e395bea6c32ce4737",
        "dab2c882ac078fcd86555fd7a118074a1ee50629",
        "7f2f2d807def8aa745b6bd48e47f98d8983cdbfd",
        "3ac623161a955484770285bb67d0254a780b53e7",
        "0f0dd83066bdf3477e56b7321a47c435fdcc7f26",
    },
    "sensor.py": {
        "8375a6b091c984e21f750ed1169423c9c8aa10d1",
        "821cd653ca0981a39c435458267392e510ab1bbc",
        "6cacbb68550f7ed3302207e08cd93bcd4499e12d",
        "6bc21732ef07f67fedee75d90ad0673a9eaa9701",
        "79e7b294f5d3fed03f7b088c6a8f015e10d08755",
        "ef98bba3870c29cdc0ef7abce5df07377c20a4eb",
        "57eade5053f7c814ea892058a8db05c4831e1e21",
        "2a8d6a0a3268cf8d4568e027fe5ab4fb4f8b6fe2",
    },
    "energy_counter.py": {
        "0848e7ab1b6692868bc2b9f381b812de4cd58fa3",
        "319bac6591bfb3ab743d1a3c1b39df9737ccc4b4",
        "0848e7ab1b6692868bc2b9f381b812de4cd58fa3",
    },
    # Old PR #5 metering parser before the R2 midnight consistency fix.
    "const.py": {
        "ca53bc8a80fca1a248322ffa46fc6ae9b890da93",
        "52d8808135430d706cc6bea3c2ecc9dbaaf7e8c4",
        "6e6dbb3676b86b0948890346f34f5c445d329acd",
    },
    "button.py": {
        "93ba5cd9d1be76ab94e8ed30419785fcb5a6c0c2",
        "06086410c4ddeeb9ed42a69009b7ee51f4a74d59",
        "a96e8ce0295b8f8ad5e2e84c8856dc811979bd40",
    },
    "manifest.json": {
        "698a4891aeabf0e3bb4acf7d4ece45c1379e1053","31c9f492c24a6ecc9703260754875f2883651935"},
    # HA core-native diagnostics.py is new; don't overwrite unknown local variants.
    "diagnostics.py": {"cdbf7a4ea7e25aa9e295038c69ada5e0ef01d57b"},
}
FILES = (*BASE_HASHES, "energy_counter.py", "const.py", "button.py",
         "manifest.json", "diagnostics.py", "coordinator.py",
         "accounting_runtime.py", "accounting_ledger.py", "energy_reconciliation.py",
         "metering_interval.py", "translations/en.json", "translations/ru.json")
DEPLOYED_HASHES = {'sensor.py': ['606d63d626e179eadcce9be8e45ac851ebed2929c769546424b26bfea8e126f0', 'c57b2d8330ce358afc660a872ca4cc691962b575cda77872128aa2e279155f40', 'e6264aac644bcb5d1100bc1fbd540e3da45badb87af74f3eb230129ddbae6d88'], 'accounting_ledger.py': ['a6555da2c8f9e302e2c69ac157a8b43baba5b3e8a98b9e507c17686f438ee493'], 'accounting_runtime.py': ['e5ff6507bb039a842e475598a09f4f6865fe4a8fe87ef3f8e79ab613e061c90b', '1a569f914b5bdf01589045708460d458415539a2840fc1f0ee585d690e1c62a1', 'a320fa319c1fa47c3e7b410e92ce846ae1a6e8b71d3da08c87eb77ee1e3bcc84'], 'energy_reconciliation.py': ['c2eb510d209933cbc40f6b01b2e3646e0bc121861a3a09187d230b97fe9b48c2'], 'metering_interval.py': ['589504af54f6e793bfcdad56cb3dd07a8808ae618380ce4f0c57836095b70b0e'], 'translations/ru.json': ['529dc6a2557a240c49fbd4484acfccaaf185b1507f636e93b29e787196d217be', 'bbce38468ff635a31a30dbfc1a4578eec901423b7c2263bffad598a7b11e6f7f'], 'translations/en.json': ['376684dfb736bf2fc328a1c0038c5bdcf64c4c66ed6d4002f0b27998a02c2663', '6cfe8af4cd1123d72d245e896510a5dc72d5c75e1d7ce3f1c03c5d49af5d2bf9'], '__init__.py': ['694437b018571b2d8affcb5775512e206df0fd0bf2bf021ba9e5311be1ec2eb1', 'c1f90385cb66a4a31079db1604b6b20305124c521dc6e827cb69659d4fe81403', '64cd86865bb3fa812fcab7c233b37c95f6f7f34a79818b44437bace4bb97600b'], 'coordinator.py': ['0d6756096b9afdf1d292b7dcba21bd6121fd3b3bed7d5538094e523e4651464e'], 'diagnostics.py': ['3be21fd5c7f9a12d87109a3d00951ba0f0110f77ee9ddd03a940c9819b730dea']}
LEGACY_INIT_HASH = "29d8217fa113a3b2eb4db3b7d85eaaa8526baed67c8aa034d0d94f89fa978dfe"
LEGACY_INIT_BLOCK = b'        # 0.20.1 incorrectly disabled the two existing System heating-flow\n        # numbers. Undo that migration before their platform is set up, while\n        # respecting any entries the user explicitly disabled.\n        registry = er.async_get(hass)\n        for zone in device.zone_numbers:\n            for label in ("temperature", "offset"):\n                unique_id = (\n                    f"{device.gateway}-Ariston System heating flow "\n                    f"{label} {zone}-{zone}"\n                )\n                entity_id = registry.async_get_entity_id("number", DOMAIN, unique_id)\n                registered = registry.async_get(entity_id) if entity_id else None\n                if (\n                    registered\n                    and registered.config_entry_id == entry.entry_id\n                    and registered.disabled_by == er.RegistryEntryDisabler.INTEGRATION\n                ):\n                    registry.async_update_entity(entity_id, disabled_by=None)\n\n'
SETUP_MARKER = b'        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--ref", default=PINNED_REF,
                        help="Full immutable GitHub commit SHA (default: tested update)")
    source.add_argument("--source-dir", type=Path, help="Local checkout (for testing)")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--restart", action="store_true", help="Check configuration and restart HA Core")
    parser.add_argument("--check", action="store_true", help="Validate without writing")
    parser.add_argument("--no-pause", action="store_true", help="Do not wait for Enter")
    args = parser.parse_args()
    if args.ref and not re.fullmatch(r"[0-9a-f]{40}", args.ref):
        raise SystemExit("Нужен полный SHA коммита из 40 символов.")
    if args.config is None:
        args.config = next((p for p in (Path("/homeassistant"), Path("/config"))
                            if (p / "custom_components/ariston/manifest.json").is_file()), Path("/config"))
    target = args.config / "custom_components/ariston"
    if not (target / "manifest.json").is_file():
        raise SystemExit(f"Интеграция не найдена: {target}")

    replacements = {}
    originals = {}
    for name in FILES:
        if args.source_dir:
            data = (args.source_dir / "custom_components/ariston" / name).read_bytes()
        else:
            url = (
                "https://raw.githubusercontent.com/sasamix/"
                f"ariston-remotethermo-home-assistant-v3/{args.ref}/"
                f"custom_components/ariston/{name}"
            )
            with urlopen(url, timeout=45) as response:
                data = response.read()
        if name.endswith(".py"):
            compile(data, name, "exec")
        elif name == "manifest.json":
            manifest = json.loads(data)
            if manifest.get("domain") != "ariston" or manifest.get("version") != "0.20.2":
                raise SystemExit("Неожиданный manifest Ariston; ничего не изменено")
        path = target / name
        old = path.read_bytes() if path.exists() else None
        originals[name] = old
        if name == "__init__.py":
            # Preserve the existing 0.20.2 heating-number migration (if any).
            # Never enable/disable user's number/select entities from here.
            if data.count(SETUP_MARKER) != 1:
                raise SystemExit("Неожиданная структура __init__.py; ничего не изменено")
            legacy_data = data.replace(SETUP_MARKER, LEGACY_INIT_BLOCK + SETUP_MARKER, 1)
            if old == legacy_data or (old is not None and hashlib.sha256(old).hexdigest() in {LEGACY_INIT_HASH, PRIOR_LEGACY_INIT_HASH}):
                data = legacy_data
                print("Основа: релиз v0.20.2; миграция отопления сохранена")
            elif old is not None and old.count(LEGACY_INIT_BLOCK) == 1:
                # Prior energy patch may already include the exact migration.
                # Preserve it only if the underlying original is recognized.
                plain = old.replace(LEGACY_INIT_BLOCK, b"", 1)
                plain_blob = hashlib.sha1(
                    b"blob " + str(len(plain)).encode("ascii") + b"\x00" + plain
                ).hexdigest()
                if (plain_blob in PRIOR_LIVE_BLOB_SHA1["__init__.py"]
                        or hashlib.sha256(old).hexdigest() in DEPLOYED_HASHES["__init__.py"]):
                    data = legacy_data
                    print("Существующая миграция System heating flow сохранена")
        if old == data:
            print(f"Уже установлен: {name}")
            continue
        digest = hashlib.sha256(old).hexdigest() if old is not None else None
        expected = BASE_HASHES.get(name)
        accepted = {expected, PRIOR_PATCH_HASHES.get(name), *DEPLOYED_HASHES.get(name, [])}
        if name == "__init__.py":
            accepted.update({LEGACY_INIT_HASH, PRIOR_LEGACY_INIT_HASH})
        blob_sha1 = (
            hashlib.sha1(
                b"blob " + str(len(old)).encode("ascii") + b"\x00" + old
            ).hexdigest()
            if old is not None else None
        )
        if (digest not in accepted
                and blob_sha1 not in PRIOR_LIVE_BLOB_SHA1.get(name, set())
                and not (name == "__init__.py"
                         and old is not None
                         and old.count(LEGACY_INIT_BLOCK) == 1
                         and hashlib.sha1(
                             b"blob " +
                             str(len(old.replace(LEGACY_INIT_BLOCK, b"", 1))).encode("ascii") +
                             b"\x00" + old.replace(LEGACY_INIT_BLOCK, b"", 1)
                         ).hexdigest() in PRIOR_LIVE_BLOB_SHA1["__init__.py"])):

            raise SystemExit(
                f"ОСТАНОВКА: {name} отличается от проверенной основы.\n"
                f"SHA256: {digest}\nНичего не изменено. Пришлите этот вывод."
            )
        replacements[name] = data
    if not replacements:
        print("Исправление уже установлено.")
        return
    if args.check:
        print("Проверка пройдена. Файлы не изменены:", ", ".join(replacements))
        return

    if args.restart and shutil.which("ha") is None:
        raise SystemExit("Команда ha не найдена; файлы не изменены.")
    backup = args.config / "ariston_backups" / datetime.now().strftime("energy_%Y%m%d_%H%M%S_%f")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target, backup)
    print("Резервная копия:", backup)
    written = []
    try:
        with tempfile.TemporaryDirectory(prefix=".energy-stage-", dir=target.parent) as staging:
            for name, data in replacements.items():
                path = Path(staging) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o644)
            for name in replacements:
                if (target / name).exists() and (target / name).read_bytes() != originals[name]:
                    raise RuntimeError(f"{name} изменился во время установки")
                (target / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(staging) / name).replace(target / name)
                written.append(name)
        if args.restart:
            subprocess.run(["ha", "core", "check"], check=True, timeout=180)
    except BaseException:
        for name in reversed(written):
            if originals[name] is None:
                (target / name).unlink(missing_ok=True)
            else:
                shutil.copy2(backup / name, target / name)
        raise
    print("Установлено:", ", ".join(replacements))
    if args.restart:
        print("Перезапуск HA Core...", flush=True)
        subprocess.run(["ha", "core", "restart"], check=True, timeout=180)
    else:
        print("Версия и ID сохранены. Теперь перезапустите Home Assistant Core.")


class _Tee:
    """Write every message both to the terminal and a durable log file."""

    def __init__(self, stream, file):
        self.stream = stream
        self.file = file

    def write(self, value):
        self.stream.write(value)
        self.file.write(value)
        self.file.flush()

    def flush(self):
        self.stream.flush()
        self.file.flush()


def run():
    log_path = Path("/config/ariston_update.log")
    if not log_path.parent.is_dir():
        log_path = Path("/tmp/ariston_update.log")

    with log_path.open("a", encoding="utf-8") as log:
        tee_out = _Tee(sys.stdout, log)
        tee_err = _Tee(sys.stderr, log)
        with redirect_stdout(tee_out), redirect_stderr(tee_err):
            print("\\n=== Ariston Energy update:", datetime.now().isoformat(), "===")
            print("Pinned source:", PINNED_REF)
            print("Journal:", log_path)
            code = 0
            try:
                main()
            except SystemExit as err:
                code = err.code if isinstance(err.code, int) else 1
                if code:
                    if not isinstance(err.code, int):
                        print(str(err))
                    print("Установка не выполнена: проверка отклонила файлы.")
            except Exception:
                code = 1
                print("ОШИБКА: установка завершилась неуспешно.")
                traceback.print_exc()
            print("=== Код завершения:", code, "===")
            print("Журнал:", log_path)
            if sys.stdin.isatty() and "--no-pause" not in sys.argv:
                try:
                    input("Нажмите Enter для выхода из установщика...")
                except (EOFError, KeyboardInterrupt):
                    pass
            return code


if __name__ == "__main__":
    sys.exit(run())
