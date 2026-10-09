from app.db.schema import DBCriticalPage
from app.db.services.base_crud_service import BaseCRUDService
from app.models.critical_page_models import CriticalPageCreate, CriticalPageRead, CriticalPageUpdate


class CriticalPageService(BaseCRUDService[DBCriticalPage, CriticalPageRead, CriticalPageCreate, CriticalPageUpdate]):
    """Reads and writes the critical pages watched on each website. Only the standard operations are needed."""

    table = DBCriticalPage
    read_model = CriticalPageRead
