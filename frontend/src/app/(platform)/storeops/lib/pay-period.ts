// PROMOTED to src/lib/pay-period.ts (Phase W2, owner directive 2026-09-01: one shared pay-period
// resolver for hours approval / schedule / payroll / payroll tax / payroll expenses). This file is
// kept as a pure re-export so the pre-existing consumers (storeops payroll / payroll-change-log /
// salary-advances / reports) keep working unchanged; new code should import '@/lib/pay-period'.
export * from '@/lib/pay-period'
