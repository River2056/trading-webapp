from importlib.resources import files


def test_canonical_backend_package_contains_all_migrations() -> None:
    migrations = files("backend").joinpath("migrations")
    names = sorted(item.name for item in migrations.iterdir() if item.name.endswith(".sql"))
    assert names == [
        "001_bootstrap.sql",
        "002_round_plans.sql",
        "003_planning_settings.sql",
        "004_paper_trading.sql",
        "005_restart_safety.sql",
        "006_round_lifecycle.sql",
        "007_analytics_history_indexes.sql",
        "008_operational_contentions.sql",
    ]