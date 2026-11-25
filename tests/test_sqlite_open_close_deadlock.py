"""SQLite 3.51.0~3.51.2 unix VFS 열기/닫기 교착 회귀 테스트.

연결-호출마다 열고 닫는 get_conn() 패턴을 2~3개 스레드로 반복하면, 우회책이 없을 때
3.51.0에서는 1초 이내에 영구 교착(앱 전체 DB 무응답)이 재현된다.
교착 시 pytest가 멈추지 않도록 별도 프로세스에서 실행하고 제한 시간 후 판정한다.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

STRESS_SCRIPT = textwrap.dedent(
    """
    import os, sys, threading, time
    sys.path.insert(0, os.environ["SBAI_ROOT"])
    import app.db as db
    if os.environ.get("SBAI_DISABLE_GUARD") == "1":
        db._SQLITE_OPEN_CLOSE_DEADLOCK_BUG = False  # 대조군: 우회책 비활성화
    from app.config import seed_defaults
    seed_defaults(); db.init_db()

    stop = time.monotonic() + float(os.environ["SBAI_SECONDS"])
    def worker(i):
        n = 0
        while time.monotonic() < stop:
            with db.get_conn() as c:
                c.execute("SELECT COUNT(*) FROM token_usage").fetchone()
                if n % 7 == 0:
                    c.execute(
                        "INSERT INTO rag_meta(key, value) VALUES ('stress', ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (str(i),),
                    )
            n += 1

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(3)]
    for t in threads: t.start()
    for t in threads: t.join(float(os.environ["SBAI_SECONDS"]) + 10)
    stuck = sum(t.is_alive() for t in threads)
    print("STUCK" if stuck else "OK", flush=True)
    os._exit(2 if stuck else 0)
    """
)


def _run_stress(tmp_path: Path, seconds: float, disable_guard: bool = False) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "SOCIALBID_DATA_DIR": str(tmp_path / "data"),
        "SBAI_ROOT": str(ROOT),
        "SBAI_SECONDS": str(seconds),
        "SBAI_DISABLE_GUARD": "1" if disable_guard else "0",
    }
    try:
        return subprocess.run(
            [sys.executable, "-c", STRESS_SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            timeout=seconds + 40,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("SQLite 연결 열기/닫기 스트레스 중 프로세스가 응답하지 않음(교착 의심)")


def test_guard_enabled_exactly_on_affected_sqlite_versions() -> None:
    import app.db as db

    affected = (3, 51, 0) <= sqlite3.sqlite_version_info < (3, 51, 3)
    assert db._SQLITE_OPEN_CLOSE_DEADLOCK_BUG is affected


def test_concurrent_open_close_does_not_deadlock(tmp_path: Path) -> None:
    res = _run_stress(tmp_path, seconds=4.0)
    assert res.returncode == 0, f"교착 발생 (sqlite {sqlite3.sqlite_version}): {res.stdout} {res.stderr[-500:]}"
    assert "OK" in res.stdout
