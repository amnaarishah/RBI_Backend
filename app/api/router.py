from fastapi import APIRouter, Depends
from app.api.deps import authorize
from app.api.endpoints import equipment, inspection, cml, asme, auth, tank

api_router = APIRouter(prefix="/api/v1")

# Public: only the sign-in endpoint
api_router.include_router(auth.public_router)

# Everything else requires a valid token and the role that the method/path needs (see api/deps.py)
protected = APIRouter(dependencies=[Depends(authorize)])
protected.include_router(auth.router)
protected.include_router(equipment.router)
protected.include_router(inspection.router, prefix="/inspection", tags=["Inspection Register"])
protected.include_router(cml.router, prefix="/cml", tags=["CML / Thickness Readings"])
protected.include_router(asme.router)
protected.include_router(tank.router)
api_router.include_router(protected)
