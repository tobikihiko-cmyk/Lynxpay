"""Domain routers for the LynxPay API."""

from fastapi import APIRouter

from app.routers import (
    api_keys,
    callbacks,
    catalog,
    credentials,
    invoices,
    merchants,
    organizations,
    payments,
    reconciliation,
    reversals,
    webhooks,
)

router = APIRouter()
router.include_router(organizations.router)
router.include_router(merchants.router)
router.include_router(credentials.router)
router.include_router(api_keys.router)
router.include_router(catalog.router)
router.include_router(payments.router)
router.include_router(invoices.router)
router.include_router(reconciliation.router)
router.include_router(reversals.router)
router.include_router(callbacks.router)
router.include_router(webhooks.router)

__all__ = ["router"]
