'use client'
// DM Closing Verification — Daily Closing module. Renders the shared verification component, then
// asks the flowchart what comes next (owner directive 2026-09-10: "the current module should ask the
// chart what do they want to do next — so changes needed in the dm verify modules also").
//
// The prompt is added HERE rather than inside DailyClosingVerify because that component is mounted at
// two routes (`/closing/verify` and `/storeops/closing`) and the next step is a property of WHERE YOU
// ARE in the workflow, not of the verification table. Wrapping keeps the shared component free of a
// hard-coded "you are at /closing/verify".
import DailyClosingVerify from '@/components/DailyClosingVerify'
import { WorkflowNext } from '@/components/WorkflowNext'

export default function ClosingVerifyPage() {
  return (
    <>
      <DailyClosingVerify />
      <WorkflowNext here="/closing/verify" />
    </>
  )
}
