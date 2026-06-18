"""DM (District Manager) store-visit module — Phase 1.

A DM (= the Market Manager role, scope: market) logs a store visit: check-in with GPS + time,
the scheduled vs actual sales rep + discrepancy reason, a management-configurable inspection
checklist (storeops.checklist_items), accessories to order (vAccessorize), a "clean store" photo,
and submit. Photos live in the Supabase Storage bucket `store-visits`; only paths are stored,
served to the UI as short-lived signed URLs. Tables: storeops.store_visits / _responses /
_accessories + storeops.checklist_items (migration 027).
"""
