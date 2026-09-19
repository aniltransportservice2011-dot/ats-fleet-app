-- Reverts trips.guarantee_applied — the "Apply to billing" slider feature was removed in favor
-- of a simpler rule: Guarantee Quantity is only ever filled in on a trip when it genuinely
-- applies, so its mere presence (non-zero) is now enough to mean "bill off this amount" (see
-- _trip_freight() in app.py). No live trip ever had a non-default value in this column (checked
-- before dropping it), so this is a clean removal with no data loss.
ALTER TABLE trips DROP COLUMN guarantee_applied;
