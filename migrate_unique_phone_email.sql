-- Adds the database-level uniqueness backstop for users.phone/users.email (app-level checks via
-- _duplicate_user_contact() in app.py are already live and enforced on every create/update path).
--
-- DO NOT RUN THIS AGAINST THE REAL fleet.db YET. As of writing, two real accounts already share
-- the same phone number:
--     Abinash (id 4) and Muna (id 7) both have phone = '9668348621'
-- SQLite refuses to build a UNIQUE index over data that already violates it, so this migration
-- will fail outright until that's resolved one way or another (confirm which account should keep
-- the number and clear/replace it on the other, e.g.:
--     UPDATE users SET phone = NULL WHERE id = 7;   -- or whichever real number Muna should have
-- ). Re-check for any other conflicts that may exist by the time this actually runs:
--     SELECT phone, COUNT(*) FROM users WHERE phone IS NOT NULL GROUP BY phone HAVING COUNT(*) > 1;
--     SELECT lower(email), COUNT(*) FROM users WHERE email IS NOT NULL GROUP BY lower(email) HAVING COUNT(*) > 1;
-- Both must return zero rows before running the two CREATE UNIQUE INDEX statements below.
CREATE UNIQUE INDEX idx_users_phone_unique ON users(phone) WHERE phone IS NOT NULL;
CREATE UNIQUE INDEX idx_users_email_unique_ci ON users(lower(email)) WHERE email IS NOT NULL;
