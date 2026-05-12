import httpx
from fastapi import APIRouter

router = APIRouter(prefix="/api/employees", tags=["employees"])

EMPLOYEES = {
    "tech_lead":       {"name": "技术负责人", "port": 9000},
    "mechanical":      {"name": "机械工程师", "port": 9001},
    "hardware":        {"name": "硬件工程师", "port": 9002},
    "firmware":        {"name": "固件工程师", "port": 9003},
    "algorithm":       {"name": "算法工程师", "port": 9004},
    "product_manager": {"name": "产品经理",   "port": 9005},
    "testing":         {"name": "测试工程师", "port": 9006},
    "cost":            {"name": "成本工程师", "port": 9007},
    "project_manager": {"name": "项目经理",   "port": 9008},
}


@router.get("")
async def list_employees():
    result = []
    for key, info in EMPLOYEES.items():
        status = "offline"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"http://localhost:{info['port']}/health")
                if r.status_code == 200:
                    status = "online"
        except Exception:
            pass
        result.append({"key": key, "name": info["name"], "port": info["port"], "status": status})
    return result
