"""Applicable only for cloud provisioning.

This file is used only when deployed to the cloud with a multi-region deployment.

Azure Container Apps Job entrypoint for the scheduled rollup.
"""

import logging

from app.jobs.summary_job import scheduled_rollup_all_users


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    created = scheduled_rollup_all_users()
    print(f"scheduled rollups created: {created}")


if __name__ == "__main__":
    main()
