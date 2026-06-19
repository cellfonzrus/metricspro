"""Daily Closing module — DM store-visit Phase 3.

Ingests the daily closing sheet (the Google "Envelopes Data" form export: one row per rep per day),
gives the DM an evening verification view (per-store totals + who's missing vs the schedule), and
reconciles the entered counts against B2B actual daily sales (commcalc.daily_sales_actuals). Tables:
commcalc.daily_closing + commcalc.daily_closing_verification (migration 029). Also supports in-app
manual row entry for the eventual switch off the Google sheet.
"""
