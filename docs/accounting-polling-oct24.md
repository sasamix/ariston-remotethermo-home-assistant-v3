# Isolate live accounting from tariff and Recorder waits

Previously every live refresh waited for the tariff mutex, pending transaction recovery, cost sensor registration, tariff history, and a full queued Recorder reconciliation. While waiting, `busy` suppressed every subsequent live refresh. A stalled tariff or Recorder operation could freeze all eight counters until reload.

Confirmed saved readings now advance using the change in validated dated consumption curves, preserving their lifetime offsets and IDs. Save precedes publication. Exactly one background reconciliation waits for tariff/Recorder work; its completion cannot overwrite more recent readings. An unloaded queued transaction is rejected before execution. The mutex remains held until an already queued transaction actually finishes, preventing concurrent tariff writes.

First-time accounting still requires successful baseline reconciliation before publishing unconfirmed values. Historical reconciliation continues to create backups and update statistics; this is not a disablement of automatic history repair. `sensor.ariston_accounting_status` distinguishes pending statistics from verified statistics and includes the source coverage separately.

Regression coverage: held tariff mutex; unresolved Recorder future and a later source revision; missing cost sensors; pending tariff recovery; unloading a lock waiter. Existing database, source, lifecycle, installer, and startup tests remain required. Version and entity IDs are unchanged.
