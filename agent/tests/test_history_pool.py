import time
from pathlib import Path
from agent.history_pool import PersistentHistoryPool


def test_history_pool_account_isolation(tmp_path: Path):
    db_file = tmp_path / "test_pool.json"
    pool = PersistentHistoryPool(storage_file=db_file)

    # 账号 11 写入博主
    pool.mark_visited("账号: 11", "satle40")
    pool.mark_visited("账号: 11", "terakimos_bko")

    # 账号 22 写入博主
    pool.mark_visited("账号: 22", "user_22_target")

    # 验证账号 11 能看到自己的，看不到账号 22 的
    assert pool.is_visited("账号: 11", "satle40") is True
    assert pool.is_visited("账号: 11", "terakimos_bko") is True
    assert pool.is_visited("账号: 11", "user_22_target") is False

    # 验证账号 22 能看到自己的，看不到账号 11 的
    assert pool.is_visited("账号: 22", "user_22_target") is True
    assert pool.is_visited("账号: 22", "satle40") is False

    handles_11 = pool.load_account_handles("账号: 11")
    assert handles_11 == {"satle40", "terakimos_bko"}

    handles_22 = pool.load_account_handles("账号: 22")
    assert handles_22 == {"user_22_target"}


def test_history_pool_retention_expiration(tmp_path: Path):
    db_file = tmp_path / "test_pool_expire.json"
    # 保留期设为 2 秒
    pool = PersistentHistoryPool(storage_file=db_file, retention_seconds=2)

    pool.mark_visited("账号: 11", "old_user")
    assert pool.is_visited("账号: 11", "old_user") is True

    # 等待 2.2 秒使其过期
    time.sleep(2.2)

    # 再次查询，应自动过期被清理
    assert pool.is_visited("账号: 11", "old_user") is False
    assert len(pool.load_account_handles("账号: 11")) == 0


def test_history_pool_corrupt_file_raises_and_preserves(tmp_path: Path):
    from agent.history_pool import HistoryPoolError
    db_file = tmp_path / "corrupted_pool.json"
    corrupt_content = "NOT_A_VALID_JSON_{{[["
    db_file.write_text(corrupt_content, encoding="utf-8")

    pool = PersistentHistoryPool(storage_file=db_file)
    import pytest
    with pytest.raises(HistoryPoolError):
        pool.is_visited("acc_1", "some_user")

    with pytest.raises(HistoryPoolError):
        pool.mark_visited("acc_1", "some_user")

    # 验证原损坏文件内容未被空字典冲掉
    assert db_file.read_text(encoding="utf-8") == corrupt_content


def test_history_pool_concurrent_writes_no_lost_updates(tmp_path: Path):
    import concurrent.futures
    db_file = tmp_path / "concurrent_pool.json"
    pool = PersistentHistoryPool(storage_file=db_file)

    def write_worker(idx: int):
        for j in range(10):
            pool.mark_visited(f"acc_{idx}", f"user_{idx}_{j}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(write_worker, i) for i in range(5)]
        for f in futures:
            f.result()

    # 验证 5 个账号，每个账号 10 个 handle 全部被持久化，零丢失
    for i in range(5):
        handles = pool.load_account_handles(f"acc_{i}")
        assert len(handles) == 10
        for j in range(10):
            assert f"user_{i}_{j}" in handles


def test_history_pool_empty_whitespace_and_null_bytes_handled_safely(tmp_path: Path):
    for idx, invalid_content in enumerate(["", "   \n\t  ", "\x00\x00\x00", "\ufeff", "\ufeff   \n"]):
        test_file = tmp_path / f"pool_empty_{idx}.json"
        test_file.write_text(invalid_content, encoding="utf-8")
        pool = PersistentHistoryPool(storage_file=test_file)
        assert pool.is_visited("acc_1", "someone") is False
        assert pool.load_account_handles("acc_1") == set()
        # 写入新记录应能正常保存
        pool.mark_visited("acc_1", "new_user")
        assert pool.is_visited("acc_1", "new_user") is True


