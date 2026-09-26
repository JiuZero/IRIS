import typer

from iris.config import get_settings
from iris.db.engine import get_engine, init_db
from iris.log import get_logger, setup_logging

app = typer.Typer(help="IRIS - IoT Rehosting & Interconnection Simulator", no_args_is_help=True)
db_app = typer.Typer(help="metadata database operations")
app.add_typer(db_app, name="db")

log = get_logger(__name__)


@db_app.command("init")
def db_init() -> None:
    """Create all IRIS metadata tables."""
    settings = get_settings()
    engine = get_engine(settings.database_url)
    init_db(engine)
    typer.echo(f"database initialized: {settings.database_url}")


@db_app.command("check")
def db_check() -> None:
    """Verify database connectivity."""
    from sqlalchemy import text

    settings = get_settings()
    engine = get_engine(settings.database_url)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    typer.echo("database connection OK")


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    app()


if __name__ == "__main__":
    main()