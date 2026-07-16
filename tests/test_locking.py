from pathlib import Path

import pytest

from second_brain_protocol.locking import AlreadyRunning, single_instance


def test_competing_windows_lock_reports_already_running_without_unlock_error(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "pipeline.lock"

    with single_instance(lock_path):
        with pytest.raises(AlreadyRunning, match="Another second-brain run is active"):
            with single_instance(lock_path):
                raise AssertionError("The competing lock must not be acquired")
