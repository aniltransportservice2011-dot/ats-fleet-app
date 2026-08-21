"""Phase 1d: Employee Ledger integrity suite -- opening balance, Salary Paid, Advance Given/
Repaid, running balance correctness, and specifically the NULL-date exclusion risk found while
auditing dead payroll routes. REPORT ONLY -- this script documents findings, it does not fix
anything (per explicit instruction: point issues out, fix them one by one afterward).

Run end-to-end against a disposable copy of fleet.db; the real file is never touched.

Usage:
    python3 test_employee_ledger.py
"""
import os
import shutil
import sys
import sqlite3
import re

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DB = os.path.join(REPO_DIR, 'fleet.db')
TEST_DB = os.path.join(REPO_DIR, '_test_employee_ledger.db')

_fail_count = 0
_ok_count = 0
_findings = []


def _ok(msg):
    global _ok_count
    _ok_count += 1
    print(f"[OK]   {msg}")


def _fail(msg):
    global _fail_count
    _fail_count += 1
    print(f"[FAIL] {msg}")
    _findings.append(msg)


def _approx(a, b, tol=0.01):
    return abs((a or 0) - (b or 0)) <= tol


def main():
    if not os.path.exists(REAL_DB):
        print(f"No fleet.db found at {REAL_DB} -- aborting.")
        sys.exit(1)
    shutil.copyfile(REAL_DB, TEST_DB)
    print(f"Working on disposable copy: {TEST_DB} (real fleet.db untouched)\n")

    sys.path.insert(0, REPO_DIR)
    import app as appmod

    def fake_get_db():
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        return conn
    appmod.get_db = fake_get_db
    appmod.app.config['TESTING'] = True
    client = appmod.app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = 1

    def raw():
        conn = sqlite3.connect(TEST_DB)
        conn.row_factory = sqlite3.Row
        return conn

    print("=" * 72)
    print("GROUP E: Employee Ledger -- opening balance, salary, advances (live route only)")
    print("=" * 72)

    emp_name = 'FinIntegrityEmployeeX'
    conn = raw()
    conn.execute("INSERT INTO employees (name, type, basic_salary, opening_balance, opening_balance_date, created_at) VALUES (?,?,?,?,?,?)",
                 (emp_name, 'Driver', 15000, 1000, '2029-01-01', '2029-01-01 00:00:00'))
    conn.commit()
    conn.close()

    def ledger_balances():
        html = client.get(f'/employee/{emp_name}').get_data(as_text=True)
        m_adv = re.search(r'Advance outstanding.*?₹([\d,]+)', html, re.S)
        m_sal = re.search(r'Total salary paid.*?₹([\d,]+)', html, re.S)
        return html, (float(m_adv.group(1).replace(',', '')) if m_adv else None), \
                      (float(m_sal.group(1).replace(',', '')) if m_sal else None)

    html0, adv0, sal0 = ledger_balances()
    if adv0 is not None:
        _ok(f"E1: Opening balance renders (advance_balance starting point picked up: {adv0})")
    else:
        _fail("E1: Could not find 'Advance Balance' label on /employee/<name> page -- template label text may have changed since this test was written")

    # E2: real "Pay Salary" quick-add, via the actual live route (/employee/<employee> POST
    # entry_kind=salary) -- this is the ONLY live path that writes to `salaries` today; the
    # bulk Process Payroll route and its Mark Paid routes are dead code (confirmed: zero
    # template references to process_payroll, mark-paid, mark_salary_paid, bulk-mark-paid
    # anywhere in templates/).
    client.post(f'/employee/{emp_name}', data={
        'entry_kind': 'salary', 'date': '2029-02-05', 'amount': '15000', 'payment_mode': 'Bank',
    })
    html1, adv1, sal1 = ledger_balances()
    if _approx(sal1, 15000):
        _ok(f"E2: Salary Paid via the live /employee/<name> route shows correctly in Total Salary Paid (got {sal1})")
    else:
        _fail(f"E2: expected Total Salary Paid 15000, got {sal1}")
    if _approx(adv1, adv0):
        _ok(f"E2b: Salary Paid correctly does NOT move the Advance Balance (confirmed separate from advances, by design)")
    else:
        _fail(f"E2b: Advance Balance changed from {adv0} to {adv1} after a Salary Paid entry -- unexpected coupling")

    # E3: Advance Given, then partial Advance Repaid -- balance should net correctly.
    client.post(f'/employee/{emp_name}', data={
        'entry_kind': 'given', 'date': '2029-02-10', 'amount': '5000', 'notes': 'Advance for medical',
    })
    html2, adv2, sal2 = ledger_balances()
    if _approx(adv2, adv0 + 5000):
        _ok(f"E3: Advance Given (5000) correctly adds to Advance Balance (got {adv2})")
    else:
        _fail(f"E3: expected advance balance {adv0+5000}, got {adv2}")

    client.post(f'/employee/{emp_name}', data={
        'entry_kind': 'repaid', 'date': '2029-02-20', 'amount': '2000', 'notes': 'Partial repayment',
    })
    html3, adv3, sal3 = ledger_balances()
    if _approx(adv3, adv0 + 5000 - 2000):
        _ok(f"E3b: Advance Repaid (2000) correctly nets against the balance (got {adv3}, expected {adv0+3000})")
    else:
        _fail(f"E3b: expected advance balance {adv0+3000}, got {adv3}")

    # E4: date-range filtering -- does a salary/advance entry outside the filtered range
    # correctly disappear, and one inside correctly stay? (This is the mechanism that would also
    # silently DROP a NULL-date row if one ever existed for this employee -- see finding below.)
    resp_filtered = client.get(f'/employee/{emp_name}?date_from=2029-02-01&date_to=2029-02-15')
    html_f = resp_filtered.get_data(as_text=True)
    has_feb5 = '2029-02-05' in html_f
    has_feb20 = '2029-02-20' in html_f
    if has_feb5 and not has_feb20:
        _ok("E4: Date-range filter correctly includes the Feb 5 salary entry and excludes the Feb 20 (out-of-range) advance repayment")
    else:
        _fail(f"E4: date-range filter behaved unexpectedly -- Feb 5 present={has_feb5}, Feb 20 present={has_feb20} (expected True, False)")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("GROUP E-DEADCODE: confirming the NULL-date risk from the dead Process Payroll route")
    print("=" * 72)
    # This does NOT go through any live UI action -- it directly reproduces what the dead
    # process_payroll()/mark_salary_paid() code path would leave behind, to document exactly
    # what the risk is IF those routes were ever re-linked to the UI (or if old rows from before
    # they were unlinked still exist -- confirmed one such row already exists in the real
    # database: id=8, employee='Ram Prabesh', month_key='2026-08', date=NULL, payment_status=
    # 'pending', amount=0, orphaned by task #26's UI removal with no way to resolve it now).
    conn = raw()
    conn.execute("""INSERT INTO salaries (employee, month, amount, date, created_at, employee_id, month_key,
                    basic_salary, gross_salary, total_deductions, advance_recovery, net_salary, payment_status,
                    payment_date, created_by)
                    VALUES (?,?,?,NULL,?,?,?,?,?,0,0,?, 'paid', ?, ?)""",
                 (emp_name, '2029-03', 15000, '2029-03-01 00:00:00', None, '2029-03',
                  15000, 15000, 15000, '2029-03-05', 1))
    conn.commit()
    conn.close()

    resp_all = client.get(f'/employee/{emp_name}')
    html_all = resp_all.get_data(as_text=True)
    resp_ranged = client.get(f'/employee/{emp_name}?date_from=2029-03-01&date_to=2029-03-31')
    html_ranged = resp_ranged.get_data(as_text=True)
    m_sal_all = re.search(r'Total salary paid.*?₹([\d,]+)', html_all, re.S)
    sal_all = float(m_sal_all.group(1).replace(',', '')) if m_sal_all else None

    if _approx(sal_all, 15000 + 15000):
        _ok(f"E-DEADCODE-1: a NULL-date 'paid' salary row (as the dead Process Payroll route would leave) "
            f"still counts in Total Salary Paid on the unfiltered view ({sal_all}) -- the money isn't lost from the total")
    else:
        _fail(f"E-DEADCODE-1: expected unfiltered total 30000, got {sal_all}")

    m_sal_ranged = re.search(r'Total salary paid.*?₹([\d,]+)', html_ranged, re.S)
    sal_ranged = float(m_sal_ranged.group(1).replace(',', '')) if m_sal_ranged else None
    if _approx(sal_ranged, 0):
        _fail(f"E-DEADCODE-1b: CONFIRMED -- filtering the ledger to March 2029 (date_from/date_to) shows "
              f"Total Salary Paid={sal_ranged}, but a real 15000 salary payment (payment_date=2029-03-05, actually "
              f"paid within this exact range) is SILENTLY EXCLUDED because its `date` column is NULL and SQL's "
              f"`date >= ? AND date <= ?` never matches NULL. This is currently unreachable from any live UI "
              f"(the routes that create NULL-date rows -- process_payroll/mark_salary_paid/bulk_mark_salary_paid "
              f"-- are dead code, zero template references), but at least one real orphaned row from before they "
              f"were unlinked already exists in the live database (id=8, 'Ram Prabesh', 2026-08, pending, amount=0).")
    else:
        _ok(f"E-DEADCODE-1b: unexpectedly, the ranged total ({sal_ranged}) already includes the NULL-date row -- risk may not apply as described")

    print()
    print("=" * 72)
    print(f"SUMMARY: {_ok_count} passed, {_fail_count} failed")
    print("=" * 72)
    if _findings:
        print("\nFindings (not fixed, for review):")
        for f in _findings:
            print(f" - {f}")

    os.remove(TEST_DB)
    print("\nDisposable test DB removed. Real fleet.db was never touched.")


if __name__ == '__main__':
    main()
