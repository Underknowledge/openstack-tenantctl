"""Example: Load projects from a database instead of YAML.

This demonstrates implementing a custom ConfigSource to load project
configurations from a database, REST API, or any other backend.
"""

from __future__ import annotations

from typing import Any

from src import (
    ConfigSource,
    DefaultsConfig,
    RawProject,
    TenantCtl,
    build_projects,
)


class DatabaseConfigSource:
    """Load project configs from a database."""

    def __init__(self, db_connection: Any) -> None:
        """Initialize with a database connection.

        Args:
            db_connection: Database connection object (e.g., SQLAlchemy session)
        """
        self.db = db_connection

    def load_defaults(self) -> tuple[dict[str, Any], list[str]]:
        """Load pipeline-level defaults from database.

        Returns:
            Tuple of (defaults dict, list of errors)
        """
        # Example: query defaults table
        row = self.db.query("SELECT * FROM tenantctl_defaults").first()
        if not row:
            return {}, ["No defaults found in database"]

        defaults = {
            "external_network_name": row.external_network,
            "external_network_subnet": row.external_subnet,
            "enforce_unique_cidrs": row.enforce_unique_cidrs,
        }
        return defaults, []

    def load_raw_projects(self) -> tuple[list[RawProject], list[str]]:
        """Load active projects from database.

        Returns:
            Tuple of (list of RawProject objects, list of errors)
        """
        rows = self.db.query("SELECT * FROM projects WHERE active = true").all()

        projects = []
        for row in rows:
            # Convert DB row to RawProject
            projects.append(
                RawProject(
                    state_key=row.id,  # Use DB primary key as state key
                    label=f"{row.name} (DB:{row.id})",
                    source_path=f"database://projects/{row.id}",
                    data={
                        "name": row.name,
                        "resource_prefix": row.resource_prefix,
                        "description": row.description,
                        "network": {"subnet": {"cidr": row.cidr}},
                        "quotas": row.quotas_json,  # Assuming JSON column
                    },
                )
            )

        return projects, []


def main() -> None:
    """Example usage of DatabaseConfigSource."""
    # Connect to your database (pseudocode)
    # db = connect_to_database("postgresql://localhost/tenantctl")
    # For demonstration, we'll use a mock
    db = None

    # Load projects from database
    source = DatabaseConfigSource(db)
    defaults_dict, errors = source.load_defaults()
    if errors:
        print(f"Errors loading defaults: {errors}")
        return

    raw_projects, errors = source.load_raw_projects()
    if errors:
        print(f"Errors loading projects: {errors}")
        return

    # Build validated ProjectConfig objects
    defaults = DefaultsConfig.from_dict(defaults_dict)
    projects = build_projects(defaults_dict, raw_projects, state_store=None)

    # Run provisioning
    client = TenantCtl.from_cloud(cloud="mycloud")
    result = client.run(
        projects=projects,
        all_projects=projects,
        defaults=defaults,
    )

    print(f"Completed {len(result.actions)} actions")
    if result.failed_projects:
        print(f"Failed projects: {result.failed_projects}")


if __name__ == "__main__":
    main()
