from pathlib import Path
from alembic.config import Config
from alembic import command


def main():
    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")


if __name__ == "__main__":
    main()
