import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

scripts_dir = str(Path(__file__).resolve().parents[3] / "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)


@pytest.fixture(autouse=True)
def _offline_member_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    import theseus_cli

    from tests.utilities import batch_evidence

    def binding(root, base, head, ref=""):
        try:
            return batch_evidence.member_binding(root, base, head)
        except subprocess.CalledProcessError as exc:
            raise theseus_cli.TheseusError(str(exc)) from exc

    monkeypatch.setattr(theseus_cli, "member_binding", binding)


@pytest.fixture(scope="session")
def inert_scan(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    from filelock import FileLock

    from _project.scripts import inert_paths

    root = Path(__file__).resolve().parents[3]
    if "PYTEST_XDIST_WORKER" not in os.environ:
        return inert_paths.scan_tree(root)
    shared = tmp_path_factory.getbasetemp().parent / "inert_scan.json"
    with FileLock(str(shared) + ".lock"):
        if shared.is_file():
            return json.loads(shared.read_text(encoding="utf-8"))
        refs = inert_paths.scan_tree(root)
        shared.write_text(json.dumps(refs), encoding="utf-8")
        return refs


@pytest.fixture(scope="session")
def theseus_engine():
    from _project.scripts import soundness_merge_digest as digest

    unreachable = "http://127.0.0.1:9"
    previous = os.environ.get("ORACLE_PROMOTION_MIRROR")
    cache_previous = os.environ.get("THESEUS_INSTALL_CACHE")
    os.environ["ORACLE_PROMOTION_MIRROR"] = unreachable
    if cache_previous is None:
        configured_home = os.environ.get("BENCHBOX_TEST_HOST_HOME")
        account_home = Path(configured_home) if configured_home else Path(os.path.expanduser(f"~{getpass.getuser()}"))
        os.environ["THESEUS_INSTALL_CACHE"] = str(account_home / ".cache" / "theseus" / "installs")
    try:
        return digest.load_engine(Path(__file__).resolve().parents[3], "HEAD")
    except digest.ReadError as exc:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(f"the verified Theseus install is unavailable in CI: {exc}", pytrace=False)
        pytest.skip(f"the verified Theseus install is unavailable here: {exc}")
    finally:
        if previous is None:
            os.environ.pop("ORACLE_PROMOTION_MIRROR", None)
        else:
            os.environ["ORACLE_PROMOTION_MIRROR"] = previous
        if cache_previous is None:
            os.environ.pop("THESEUS_INSTALL_CACHE", None)
