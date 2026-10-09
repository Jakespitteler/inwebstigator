import logging
import uuid
from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import ColumnElement, Select, and_, not_, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.db import repository
from app.db.schema import DBChange, DBScanRun
from app.models.scan_run_models import ScanRunCreate, ScanRunRead, ScanStatus

logger: logging.Logger = logging.getLogger(__name__)


def _has_report() -> ColumnElement[bool]:
    """Matches the scans with something to tell the website's recipients: a change, or a problem with the scan.

    Returns:
        The condition, for use in a query of scans.
    """
    return or_(DBScanRun.status != ScanStatus.SUCCESS, DBScanRun.changes.any())


def _is_empty() -> ColumnElement[bool]:
    """Matches the scans that went normally but found nothing, so there is no history worth keeping.

    Returns:
        The condition, for use in a query of scans.
    """
    return not_(_has_report())


def _is_awaiting_email() -> ColumnElement[bool]:
    """Matches the scans whose report has not been emailed yet.

    Returns:
        The condition, for use in a query of scans.
    """
    return and_(DBScanRun.notified_at.is_(None), _has_report())


class ScanRunService:
    """Keeps each website's history of scans, and which scan reports are still waiting to be emailed."""

    def __init__(self, session: Session) -> None:
        """Initialises the ScanRunService with an active database session.

        Args:
            session: The SQLAlchemy database session object used for executing operations.
        """
        self._db: Session = session

    def create(self, model_create: ScanRunCreate) -> ScanRunRead:
        """Adds a scan, and everything it found, to its website's history.

        Args:
            model_create: The scan and what it found.

        Returns:
            The saved scan.

        Raises:
            IntegrityError: If the website does not exist.
        """
        scan_run_record = DBScanRun(
            **model_create.model_dump(exclude={"changes"}),
            changes=[
                DBChange(position=position, **change.model_dump())
                for position, change in enumerate(model_create.changes)
            ],
        )
        repository.add(self._db, record=scan_run_record)
        return ScanRunRead.model_validate(scan_run_record)

    def get_latest_for_website(self, website_id: uuid.UUID, limit: int) -> list[ScanRunRead]:
        """Retrieves a website's most recent scans.

        Args:
            website_id: The website whose scans to read.
            limit: The most scans to return.

        Returns:
            The scans, newest first.
        """
        statement: Select[DBScanRun] = (
            select(DBScanRun)
            .where(DBScanRun.website_id == website_id)
            .order_by(DBScanRun.scanned_at.desc())
            .limit(limit)
            .options(selectinload(DBScanRun.changes))
        )
        return [ScanRunRead.model_validate(scan_run_record) for scan_run_record in self._db.scalars(statement)]

    def get_awaiting_email(self) -> list[ScanRunRead]:
        """Retrieves every scan with a report that has not been emailed yet, across all websites.

        Returns:
            The scans, oldest first.
        """
        statement: Select[DBScanRun] = (
            select(DBScanRun)
            .where(_is_awaiting_email())
            .order_by(DBScanRun.scanned_at)
            .options(selectinload(DBScanRun.changes))
        )
        return [ScanRunRead.model_validate(scan_run_record) for scan_run_record in self._db.scalars(statement)]

    def mark_emailed(self, ids: Collection[uuid.UUID], emailed_at: datetime) -> None:
        """Records that the reports of some scans have been emailed, so they are not sent again.

        Args:
            ids: The scans whose reports were emailed.
            emailed_at: When they were emailed.
        """
        if not ids:
            return
        self._db.execute(update(DBScanRun).where(DBScanRun.id.in_(ids)).values(notified_at=emailed_at))

    def delete_older_scans(self, website_id: uuid.UUID, keep: int) -> None:
        """Deletes a website's scans older than its most recent ones, so its history does not grow forever.

        Scans that found nothing do not count towards `keep`. Only the website's newest scan is kept if it found
        nothing, so a run of empty scans does not push out the scans that found changes.

        A scan whose report has not been emailed yet is kept until it has been, so its changes are not lost.

        Args:
            website_id: The website whose history to trim.
            keep: How many of the website's most recent scans that found something to keep.
        """
        latest_scan_ids: Select[uuid.UUID] = (
            select(DBScanRun.id)
            .where(DBScanRun.website_id == website_id, not_(_is_empty()))
            .order_by(DBScanRun.scanned_at.desc())
            .limit(keep)
        )
        newest_scan_id: Select[uuid.UUID] = (
            select(DBScanRun.id)
            .where(DBScanRun.website_id == website_id)
            .order_by(DBScanRun.scanned_at.desc())
            .limit(1)
        )
        statement: Select[DBScanRun] = select(DBScanRun).where(
            DBScanRun.website_id == website_id,
            DBScanRun.id.not_in(latest_scan_ids),
            DBScanRun.id.not_in(newest_scan_id),
            not_(_is_awaiting_email()),
        )
        old_scan_runs: Sequence[DBScanRun] = self._db.scalars(statement).all()
        for scan_run_record in old_scan_runs:
            self._db.delete(scan_run_record)  # Its changes are deleted with it
        self._db.flush()
        if old_scan_runs:
            logger.info("Deleted %d old scans of website %s.", len(old_scan_runs), website_id)
