# Progress, Status and Cancel

Image jobs are owner-scoped and backed by a filesystem FIFO scheduler. UI polling reads bounded progress. Cancellation is targeted: queued jobs can be cancelled; running jobs only report success when the backend supports a real interruption.

