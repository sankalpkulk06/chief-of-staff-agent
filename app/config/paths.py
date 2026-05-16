from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class AppPaths(BaseModel):
    project_root: Path
    data_dir: Path
    chroma_dir: Path
    sqlite_dir: Path
    sqlite_db_path: Path
    cache_dir: Path
    reports_dir: Path
    credentials_dir: Path

    def ensure_directories(self) -> None:
        for directory in (self.data_dir, self.chroma_dir, self.sqlite_dir, self.cache_dir, self.reports_dir, self.credentials_dir):
            directory.mkdir(parents=True, exist_ok=True)


def build_paths(project_root: Path, data_dir: Optional[Path] = None) -> AppPaths:
    root = project_root.resolve()
    resolved_data_dir = (data_dir or (root / "data")).resolve()

    paths = AppPaths(
        project_root=root,
        data_dir=resolved_data_dir,
        chroma_dir=resolved_data_dir / "chroma",
        sqlite_dir=resolved_data_dir / "sqlite",
        sqlite_db_path=(resolved_data_dir / "sqlite" / "registry.db"),
        cache_dir=resolved_data_dir / "cache",
        reports_dir=resolved_data_dir / "reports",
        credentials_dir=resolved_data_dir / "credentials",
    )
    paths.ensure_directories()
    return paths
