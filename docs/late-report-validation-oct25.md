# Delayed report validation (October 25, 21:01)

The source-only anonymized 18:18 and 21:01 downloads are included as fixtures.
The latter API v2 and R2 responses both contain only 0.01 m³ heating and 0 m³ DHW
for 18:00–20:00. The accounting source boundary advances to 20:00; the runtime
has a successful attempt at 21:01. This is not evidence of another frozen timer.
It also does not establish that the cloud has reported all physical consumption.

New SQL regression scenarios exercise the installed 0.20.8 accounting code:

1. Real 18:18 → 21:01 data: the accepted source change is exactly 0.01/0.00 m³.
2. Hypothetical late correction at 21:05: revise the original 18:00–20:00 values,
   rebase already compiled held rows in 20:00–21:00, and price the corrected
   interval using a tariff boundary at 19:00 (76 then 100 KZT/m³). Do not charge
   the corrected amount in the arrival hour. A repeated repair is idempotent.
3. Hypothetical next-day calendar revision at 02:05: a lower total replaces the
   previous day's amount. The new day's 00:00–02:00 consumption stays zero.
   Both hourly and five-minute tables agree, with repeated repair idempotent.

Scenarios 2 and 3 are synthetic test inputs, not predictions about future reports.
Tests use SQLite transactions and the same production reconciliation planner;
this is not a claim of a live test against the user's MariaDB or cloud account.
The runtime code and manifest version are unchanged. Another installation or
restart is not justified by the observed 21:01 response alone.
