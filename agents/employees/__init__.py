# employees/__init__.py
from .mechanical import run as mechanical
from .hardware import run as hardware
from .firmware import run as firmware
from .algorithm import run as algorithm
from .testing import run as testing
from .cost import run as cost
from .product_manager import run as product_manager
from .project_manager import run as project_manager
from .tech_lead import run as tech_lead

REGISTRY: dict[str, callable] = {
    "mechanical": mechanical,
    "hardware": hardware,
    "firmware": firmware,
    "algorithm": algorithm,
    "testing": testing,
    "cost": cost,
    "product_manager": product_manager,
    "project_manager": project_manager,
    "tech_lead": tech_lead,
}
