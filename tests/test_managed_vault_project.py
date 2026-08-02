from pathlib import Path

from second_brain_protocol.collector import managed_vault_project
from second_brain_protocol.session_attribution import ProjectSessionResolver


def test_managed_vault_project_keeps_the_private_brain_separate_from_public_protocol(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "YOUR_NAME Second Brain"
    public_protocol = tmp_path / "Projects" / "External-Protocol-Project"
    vault.mkdir()
    public_protocol.mkdir(parents=True)
    managed = managed_vault_project(vault, name="YOUR_NAME Second Brain")
    public = {
        "id": "project-public-protocol",
        "name": "External Protocol Project",
        "classification": "first-party",
        "local_path": str(public_protocol),
    }

    resolver = ProjectSessionResolver([managed, public], [])

    assert managed["managed_vault"] is True
    assert managed["name"] == "YOUR_NAME Second Brain"
    assert resolver.resolve(vault).project_id == managed["id"]
    assert resolver.resolve(vault / "Protocol" / "src").project_id == managed["id"]
    assert resolver.resolve(public_protocol).project_id == public["id"]
    assert managed["id"] != public["id"]
